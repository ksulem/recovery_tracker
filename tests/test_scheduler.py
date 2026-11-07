from datetime import UTC, datetime, time
from typing import Any

import pytest
import time_machine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import ScheduledPrompt, User
from recovery.domain import PromptKind, PromptStatus
from recovery.scheduler.service import CheckinScheduler, add_minutes, planned_jobs, shift_weekly
from recovery.texts import ru
from tests.tg import OWNER_ID

Factory = async_sessionmaker[AsyncSession]
TBILISI = "Asia/Tbilisi"  # UTC+4, no DST


def at_local(hour: int, minute: int = 0) -> datetime:
    """A moment on 2026-11-09 (Monday) given in Tbilisi local time."""
    return datetime(2026, 11, 9, hour - 4, minute, tzinfo=UTC)


async def _owner(db: Factory, **fields: Any) -> User:
    async with db() as s, s.begin():
        defaults: dict[str, Any] = {
            "telegram_id": OWNER_ID,
            "timezone": TBILISI,
            "morning_time": time(9, 30),
            "evening_time": time(21, 0),
            "onboarding_completed": True,
        }
        user = User(**(defaults | fields))
        s.add(user)
    return user


def _service(client: Any, db: Factory, **kwargs: Any) -> CheckinScheduler:
    return CheckinScheduler(AsyncIOScheduler(timezone="UTC"), client.bot, db, **kwargs)


async def _statuses(db: Factory) -> dict[str, str]:
    async with db() as s:
        rows = (await s.execute(select(ScheduledPrompt.kind, ScheduledPrompt.status))).all()
    return {kind: status for kind, status in rows}


# --- pure scheduling maths -----------------------------------------------------------
def test_triggers_fire_at_local_time() -> None:
    user = User(
        timezone=TBILISI,
        morning_time=time(9, 30),
        evening_time=time(21, 0),
        reminders_enabled=True,
        session_weekday=2,
        session_time=time(10, 0),
        session_brief_lead_hours=3,
    )
    jobs = planned_jobs(user, reminder_delay_minutes=120)
    start = datetime(2026, 11, 9, 0, 0, tzinfo=UTC)  # Monday

    def next_utc(kind: PromptKind) -> datetime:
        return jobs[kind].get_next_fire_time(None, start).astimezone(UTC)

    assert next_utc(PromptKind.MORNING) == datetime(2026, 11, 9, 5, 30, tzinfo=UTC)
    assert next_utc(PromptKind.EVENING) == datetime(2026, 11, 9, 17, 0, tzinfo=UTC)
    assert next_utc(PromptKind.MORNING_REMINDER) == datetime(2026, 11, 9, 7, 30, tzinfo=UTC)
    # Session on Wednesday 10:00 → brief Wednesday 07:00 local = 03:00 UTC.
    assert next_utc(PromptKind.SESSION_BRIEF) == datetime(2026, 11, 11, 3, 0, tzinfo=UTC)


def test_triggers_follow_dst() -> None:
    user = User(
        timezone="Europe/Berlin",
        morning_time=time(9, 0),
        evening_time=time(21, 0),
        reminders_enabled=False,
    )
    morning = planned_jobs(user, 120)[PromptKind.MORNING]
    winter = morning.get_next_fire_time(None, datetime(2026, 1, 10, tzinfo=UTC))
    summer = morning.get_next_fire_time(None, datetime(2026, 7, 10, tzinfo=UTC))
    assert winter.astimezone(UTC).hour == 8  # CET
    assert summer.astimezone(UTC).hour == 7  # CEST


@pytest.mark.parametrize(
    ("weekday", "at", "lead", "expected"),
    [
        (2, time(10), 3, (2, time(7))),
        (0, time(1), 3, (6, time(22))),
        (6, time(23, 30), 0, (6, time(23, 30))),
    ],
)
def test_shift_weekly(weekday: int, at: time, lead: int, expected: tuple[int, time]) -> None:
    assert shift_weekly(weekday, at, lead) == expected


def test_reminder_never_crosses_midnight() -> None:
    assert add_minutes(time(21), 120) == time(23)
    assert add_minutes(time(23), 120) is None
    user = User(
        timezone=TBILISI, morning_time=time(9), evening_time=time(23), reminders_enabled=True
    )
    assert PromptKind.EVENING_REMINDER not in planned_jobs(user, 120)


# --- jobs ------------------------------------------------------------------------------
async def test_reschedule_replaces_jobs(make_client: Any, db: Factory) -> None:
    user = await _owner(db, reminders_enabled=True)
    service = _service(make_client(), db)
    await service.reschedule(user)
    ids = {job.id.split(":")[1] for job in service.scheduler.get_jobs()}
    assert ids == {"morning", "evening", "morning_reminder", "evening_reminder"}

    user.reminders_enabled = False
    await service.reschedule(user)
    ids = {job.id.split(":")[1] for job in service.scheduler.get_jobs()}
    assert ids == {"morning", "evening"}


async def test_session_brief_job_needs_a_sender(make_client: Any, db: Factory) -> None:
    user = await _owner(db, session_weekday=2, session_time=time(18))

    async def sender(*_: Any) -> None:
        return None

    plain = _service(make_client(), db)
    await plain.reschedule(user)
    assert not any("session_brief" in j.id for j in plain.scheduler.get_jobs())

    with_brief = _service(make_client(), db, extra_senders={PromptKind.SESSION_BRIEF: sender})
    await with_brief.reschedule(user)
    assert any("session_brief" in j.id for j in with_brief.scheduler.get_jobs())


# --- sending ---------------------------------------------------------------------------
async def test_prompt_is_sent_once_per_day(make_client: Any, db: Factory) -> None:
    user = await _owner(db)
    client = make_client()
    service = _service(client, db)
    with time_machine.travel(at_local(9, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING)
        await service.fire(user.id, PromptKind.MORNING)  # double tick / catch-up
    assert client.texts() == [ru.OFFER_MORNING]
    assert await _statuses(db) == {"morning": PromptStatus.SENT}


async def test_no_prompt_if_checkin_already_started(make_client: Any, db: Factory) -> None:
    user = await _owner(db)
    client = make_client()
    with time_machine.travel(at_local(8, 50), tick=False):
        await client.send("/morning")
    client.clear()
    with time_machine.travel(at_local(9, 30), tick=False):
        await _service(client, db).fire(user.id, PromptKind.MORNING)
    assert client.texts() == []
    assert await _statuses(db) == {"morning": PromptStatus.SKIPPED}


async def test_one_gentle_reminder(make_client: Any, db: Factory) -> None:
    user = await _owner(db, reminders_enabled=True)
    client = make_client()
    service = _service(client, db)
    with time_machine.travel(at_local(9, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING)
    with time_machine.travel(at_local(11, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING_REMINDER)
        await service.fire(user.id, PromptKind.MORNING_REMINDER)
    assert client.texts() == [ru.OFFER_MORNING, ru.REMINDER_MORNING]
    # The reminder's "Начать" opens the check-in.
    await client.click_button(ru.BTN_START)
    assert client.last_text() == ru.Q_SLEEP


async def test_no_reminder_after_not_now(make_client: Any, db: Factory) -> None:
    user = await _owner(db, reminders_enabled=True)
    client = make_client()
    service = _service(client, db)
    with time_machine.travel(at_local(9, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING)
        await client.click_button(ru.BTN_NOT_NOW)
    with time_machine.travel(at_local(11, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING_REMINDER)
    assert ru.REMINDER_MORNING not in client.texts()
    assert (await _statuses(db))["morning_reminder"] == PromptStatus.CANCELLED


async def test_no_reminder_in_quiet_hours(make_client: Any, db: Factory) -> None:
    user = await _owner(
        db,
        reminders_enabled=True,
        evening_time=time(21, 0),
        quiet_hours_start=time(22, 30),
        quiet_hours_end=time(8, 0),
    )
    client = make_client()
    service = _service(client, db)
    with time_machine.travel(at_local(21, 0), tick=False):
        await service.fire(user.id, PromptKind.EVENING)
    with time_machine.travel(at_local(23, 0), tick=False):
        await service.fire(user.id, PromptKind.EVENING_REMINDER)
    assert client.texts() == [ru.OFFER_EVENING]
    assert (await _statuses(db))["evening_reminder"] == PromptStatus.SKIPPED


async def test_no_reminder_when_disabled(make_client: Any, db: Factory) -> None:
    user = await _owner(db, reminders_enabled=False)
    client = make_client()
    service = _service(client, db)
    with time_machine.travel(at_local(9, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING)
    with time_machine.travel(at_local(11, 30), tick=False):
        await service.fire(user.id, PromptKind.MORNING_REMINDER)
    assert client.texts() == [ru.OFFER_MORNING]


async def test_no_reminder_without_the_prompt(make_client: Any, db: Factory) -> None:
    user = await _owner(db, reminders_enabled=True)
    client = make_client()
    with time_machine.travel(at_local(11, 30), tick=False):
        await _service(client, db).fire(user.id, PromptKind.MORNING_REMINDER)
    assert client.texts() == []


async def test_catch_up_after_downtime(make_client: Any, db: Factory) -> None:
    await _owner(db)
    client = make_client()
    service = _service(client, db, catchup_window_minutes=120)
    with time_machine.travel(at_local(9, 45), tick=False):
        await service.catch_up()
        await service.catch_up()  # a second restart doesn't resend
    assert client.texts() == [ru.OFFER_MORNING]


async def test_no_catch_up_outside_the_window(make_client: Any, db: Factory) -> None:
    await _owner(db)
    client = make_client()
    with time_machine.travel(at_local(12, 0), tick=False):
        await _service(client, db, catchup_window_minutes=120).catch_up()
    assert client.texts() == []


async def test_settings_change_reschedules(make_client: Any, db: Factory) -> None:
    await _owner(db)
    holder: dict[str, CheckinScheduler] = {}

    async def hook(user: User) -> None:
        await holder["service"].reschedule(user)

    client = make_client(settings_changed=hook)
    holder["service"] = _service(client, db)
    await client.send("/settings")
    await client.click_button(ru.BTN_SET_EVENING)
    await client.click_button("22:30")
    job = holder["service"].scheduler.get_job(
        next(j.id for j in holder["service"].scheduler.get_jobs() if j.id.endswith(":evening"))
    )
    nxt = job.trigger.get_next_fire_time(None, datetime(2026, 11, 9, tzinfo=UTC))
    assert nxt.astimezone(UTC) == datetime(2026, 11, 9, 18, 30, tzinfo=UTC)


async def test_running_scheduler_executes_jobs(make_client: Any, db: Factory) -> None:
    import asyncio

    await _owner(db)
    client = make_client()
    service = _service(client, db, catchup_window_minutes=0)  # no catch-up here
    await service.start()
    try:
        assert service.scheduler.running
        morning = next(j for j in service.scheduler.get_jobs() if j.id.endswith(":morning"))
        morning.modify(next_run_time=datetime.now(UTC))  # make it due right now
        for _ in range(50):
            if client.texts():
                break
            await asyncio.sleep(0.05)
    finally:
        service.scheduler.shutdown(wait=False)
    assert client.texts() == [ru.OFFER_MORNING]
