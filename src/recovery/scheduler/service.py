"""Cron jobs in the owner's timezone (plan §1.2).

* One APScheduler ``CronTrigger`` per prompt kind, with ``timezone=user.timezone``
  (DST handled by APScheduler).
* Jobs live in memory and are rebuilt from the ``users`` table at startup and
  whenever settings change — nothing to keep in sync.
* A prompt missed while the bot was down is sent on startup if its time passed
  less than ``catchup_window_minutes`` ago.
* Every send first claims a ``scheduled_prompts`` row for (kind, local day), so
  nothing is ever sent twice.
* At most one gentle reminder per check-in: not if the check-in was started,
  not after "Не сейчас", not in quiet hours, not if reminders are off.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta
from typing import Any

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery import winddown
from recovery.clock import local_now
from recovery.conversation.engine import CheckinEngine
from recovery.db.models import User
from recovery.domain import CheckinType, PromptKind, PromptStatus
from recovery.scheduler import prompts
from recovery.telegram.keyboards import offer_keyboard
from recovery.telegram.routers.checkin import send_offer
from recovery.texts import ru
from recovery.timeparse import in_quiet_hours

logger = logging.getLogger(__name__)

# Extra senders plugged in by other modules (e.g. the session brief).
# Extra senders return False when there was nothing to send (e.g. nothing is
# parked before the walk); None or True count as sent.
Sender = Callable[[Bot, AsyncSession, User], Awaitable[bool | None]]
# Kinds that are only scheduled when a sender for them is plugged in.
NEEDS_SENDER = {PromptKind.SESSION_BRIEF, PromptKind.PRE_WALK}

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CHECKIN_OF = {
    PromptKind.MORNING: CheckinType.MORNING,
    PromptKind.EVENING: CheckinType.EVENING,
    PromptKind.MORNING_REMINDER: CheckinType.MORNING,
    PromptKind.EVENING_REMINDER: CheckinType.EVENING,
}
REMINDER_TEXT = {
    CheckinType.MORNING: ru.REMINDER_MORNING,
    CheckinType.EVENING: ru.REMINDER_EVENING,
}


def shift_weekly(weekday: int, at: time, hours_before: int) -> tuple[int, time]:
    """(Wed, 10:00) minus 3 h → (Wed, 07:00); (Mon, 01:00) minus 3 h → (Sun, 22:00)."""
    week = 7 * 24 * 60
    minutes = (weekday * 1440 + at.hour * 60 + at.minute - hours_before * 60) % week
    return minutes // 1440, time((minutes % 1440) // 60, minutes % 60)


def add_minutes(at: time, minutes: int) -> time | None:
    """Same-day time ``minutes`` later, or None if it would pass midnight."""
    total = at.hour * 60 + at.minute + minutes
    if total >= 24 * 60:
        return None
    return time(total // 60, total % 60)


def planned_jobs(
    user: User, reminder_delay_minutes: int, evening_reminder_delay_minutes: int | None = None
) -> dict[PromptKind, CronTrigger]:
    tz = user.timezone
    jobs = {
        PromptKind.MORNING: CronTrigger(
            hour=user.morning_time.hour, minute=user.morning_time.minute, timezone=tz
        ),
        PromptKind.EVENING: CronTrigger(
            hour=user.evening_time.hour, minute=user.evening_time.minute, timezone=tz
        ),
    }
    if user.reminders_enabled:
        evening_delay = evening_reminder_delay_minutes or reminder_delay_minutes
        for kind, base, delay in (
            (PromptKind.MORNING_REMINDER, user.morning_time, reminder_delay_minutes),
            (PromptKind.EVENING_REMINDER, user.evening_time, evening_delay),
        ):
            at = add_minutes(base, delay)
            if at is not None:
                jobs[kind] = CronTrigger(hour=at.hour, minute=at.minute, timezone=tz)
    if user.session_weekday is not None and user.session_time is not None:
        day, at = shift_weekly(
            user.session_weekday, user.session_time, user.session_brief_lead_hours
        )
        jobs[PromptKind.SESSION_BRIEF] = CronTrigger(
            day_of_week=DAYS[day], hour=at.hour, minute=at.minute, timezone=tz
        )
    if user.walk_time is not None:
        jobs[PromptKind.PRE_WALK] = CronTrigger(
            hour=user.walk_time.hour, minute=user.walk_time.minute, timezone=tz
        )
    return jobs


class CheckinScheduler:
    def __init__(
        self,
        scheduler: AsyncIOScheduler,
        bot: Bot,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        reminder_delay_minutes: int = 120,
        evening_reminder_delay_minutes: int | None = None,
        catchup_window_minutes: int = 120,
        extra_senders: dict[PromptKind, Sender] | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.bot = bot
        self.session_factory = session_factory
        self.reminder_delay_minutes = reminder_delay_minutes
        self.evening_reminder_delay_minutes = evening_reminder_delay_minutes
        self.catchup_window = timedelta(minutes=catchup_window_minutes)
        self.extra_senders = extra_senders or {}

    # --- job management ------------------------------------------------------------
    async def start(self) -> None:
        async with self.session_factory() as session:
            users = list((await session.scalars(select(User))).all())
        for user in users:
            await self.reschedule(user)
        self.scheduler.start()
        await self.catch_up()

    async def reschedule(self, user: User) -> None:
        """Rebuild this user's jobs from their current settings."""
        prefix = f"{user.id}:"
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()
        jobs = planned_jobs(user, self.reminder_delay_minutes, self.evening_reminder_delay_minutes)
        for kind, trigger in jobs.items():
            if kind in NEEDS_SENDER and kind not in self.extra_senders:
                continue
            self.scheduler.add_job(
                self.fire,
                trigger,
                args=[user.id, kind],
                id=f"{prefix}{kind}",
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=max(60, int(self.catchup_window.total_seconds())),
            )
        logger.info("scheduled %d jobs", len(self.scheduler.get_jobs()))

    async def forget(self, user_id: Any) -> None:
        """Drop all jobs of a deleted user."""
        prefix = f"{user_id}:"
        for job in self.scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()

    async def catch_up(self) -> None:
        """Send today's morning/evening prompt if it was missed during downtime."""
        async with self.session_factory() as session:
            users = list((await session.scalars(select(User))).all())
        for user in users:
            now = local_now(user.timezone)
            for kind, at in (
                (PromptKind.MORNING, user.morning_time),
                (PromptKind.EVENING, user.evening_time),
            ):
                due = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
                if due <= now < due + self.catchup_window:
                    await self.fire(user.id, kind)

    # --- sending -----------------------------------------------------------------
    async def fire(self, user_id: Any, kind: PromptKind) -> None:
        async with self.session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                return
            prompt_id = await prompts.claim(session, user, kind)
            if prompt_id is None:
                return  # already handled today
            await session.commit()
            try:
                sent = await self._send(session, user, kind)
            except Exception:
                logger.exception("sending %s failed", kind)
                await prompts.set_status(session, prompt_id, PromptStatus.FAILED)
                await session.commit()
                return
            await prompts.set_status(
                session, prompt_id, PromptStatus.SENT if sent else PromptStatus.SKIPPED
            )
            await session.commit()

    async def _send(self, session: AsyncSession, user: User, kind: PromptKind) -> bool:
        if kind in self.extra_senders:
            return await self.extra_senders[kind](self.bot, session, user) is not False
        checkin_type = CHECKIN_OF.get(kind)
        if checkin_type is None:
            return False
        existing = await CheckinEngine(session, user).find_today(checkin_type)
        if existing is not None:
            return False  # already started or done by hand
        if kind in (PromptKind.MORNING, PromptKind.EVENING):
            await send_offer(self.bot, user.telegram_id, checkin_type)
            return True
        # Reminder.
        if not user.reminders_enabled or winddown.is_late(user):
            return False
        if in_quiet_hours(
            local_now(user.timezone).time(), user.quiet_hours_start, user.quiet_hours_end
        ):
            return False
        main_kind = (
            PromptKind.MORNING if checkin_type == CheckinType.MORNING else (PromptKind.EVENING)
        )
        if await prompts.status_today(session, user, main_kind) != PromptStatus.SENT:
            return False  # never remind about a prompt that wasn't sent
        await self.bot.send_message(
            user.telegram_id,
            REMINDER_TEXT[checkin_type],
            reply_markup=offer_keyboard(checkin_type),
        )
        return True
