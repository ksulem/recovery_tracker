"""``scheduled_prompts`` bookkeeping: at most one prompt of each kind per
local day, whatever happens (double ticks, restarts, catch-up)."""

import uuid
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import local_today, utcnow
from recovery.db.models import ScheduledPrompt, User
from recovery.domain import CheckinType, PromptKind, PromptStatus

REMINDER_FOR = {
    CheckinType.MORNING: PromptKind.MORNING_REMINDER,
    CheckinType.EVENING: PromptKind.EVENING_REMINDER,
}


async def claim(
    session: AsyncSession, user: User, kind: PromptKind, day: date | None = None
) -> uuid.UUID | None:
    """Insert today's row for ``kind``; None if it already exists."""
    stmt = (
        insert(ScheduledPrompt)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            kind=kind,
            local_date=day or local_today(user.timezone),
            status=PromptStatus.SENDING,
            created_at_utc=utcnow(),
        )
        .on_conflict_do_nothing(constraint="uq_scheduled_prompts_daily")
        .returning(ScheduledPrompt.id)
    )
    return await session.scalar(stmt)


async def set_status(session: AsyncSession, prompt_id: uuid.UUID, status: PromptStatus) -> None:
    values: dict[str, object] = {"status": status}
    if status == PromptStatus.SENT:
        values["sent_at_utc"] = utcnow()
    await session.execute(
        update(ScheduledPrompt).where(ScheduledPrompt.id == prompt_id).values(**values)
    )


async def status_today(session: AsyncSession, user: User, kind: PromptKind) -> str | None:
    return await session.scalar(
        select(ScheduledPrompt.status).where(
            ScheduledPrompt.user_id == user.id,
            ScheduledPrompt.kind == kind,
            ScheduledPrompt.local_date == local_today(user.timezone),
        )
    )


async def cancel_reminder(session: AsyncSession, user: User, kind: CheckinType) -> None:
    """The owner tapped "Не сейчас": today's reminder must not come."""
    reminder = REMINDER_FOR.get(kind)
    if reminder is None:
        return
    prompt_id = await claim(session, user, reminder)
    if prompt_id is not None:
        await set_status(session, prompt_id, PromptStatus.CANCELLED)
