"""The list of things to discuss at the next psychologist session."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import utcnow
from recovery.db.models import DiscussionItem, User
from recovery.domain import DiscussionStatus


async def add_item(
    session: AsyncSession, user: User, text: str, message_id: uuid.UUID | None = None
) -> DiscussionItem:
    item = DiscussionItem(
        user_id=user.id,
        text=text.strip(),
        message_id=message_id,
        status=DiscussionStatus.OPEN,
        tz_name=user.timezone,
        created_at_utc=utcnow(),
    )
    session.add(item)
    await session.flush()
    return item


async def count_open(session: AsyncSession, user: User) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(DiscussionItem)
        .where(DiscussionItem.user_id == user.id, DiscussionItem.status == DiscussionStatus.OPEN)
    )
    return int(count or 0)
