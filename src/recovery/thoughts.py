"""Parked thoughts ("на потом"): written down now, dealt with later.

* One thought at a time on the evening walk, only on request.
* "Пусть полежит" is always a fine answer; after three postponements the bot
  offers — once — to move the thought to the session list.
* Unresolved thoughts are never treated as a failure.
"""

import uuid
from enum import StrEnum

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from recovery.clock import local_today, utcnow
from recovery.db.models import ParkedThought, User
from recovery.discussion import add_item
from recovery.domain import ThoughtStatus

SUGGEST_SESSION_AFTER = 3


class Action(StrEnum):
    DONE = "done"  # обдумано
    LATER = "later"  # пусть полежит
    SESSION = "session"  # на сессию
    DROP = "drop"  # неактуально


def split(text: str) -> list[str]:
    """One thought per line; blank lines ignored."""
    return [line.strip() for line in text.splitlines() if line.strip()]


async def park(
    session: AsyncSession, user: User, text: str, message_id: uuid.UUID | None = None
) -> list[ParkedThought]:
    now = utcnow()
    thoughts = [
        ParkedThought(
            user_id=user.id,
            text=line,
            message_id=message_id,
            status=ThoughtStatus.OPEN,
            tz_name=user.timezone,
            created_at_utc=now,
        )
        for line in split(text)
    ]
    session.add_all(thoughts)
    await session.flush()
    return thoughts


def _open(user: User) -> ColumnElement[bool]:
    return and_(ParkedThought.user_id == user.id, ParkedThought.status == ThoughtStatus.OPEN)


async def count_open(session: AsyncSession, user: User) -> int:
    count = await session.scalar(select(func.count()).select_from(ParkedThought).where(_open(user)))
    return int(count or 0)


async def open_thoughts(
    session: AsyncSession, user: User, limit: int | None = None
) -> list[ParkedThought]:
    stmt = select(ParkedThought).where(_open(user)).order_by(ParkedThought.seq)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.scalars(stmt)).all())


async def get(session: AsyncSession, user: User, thought_id: str) -> ParkedThought | None:
    try:
        thought = await session.get(ParkedThought, uuid.UUID(hex=thought_id))
    except ValueError:
        return None
    if thought is None or thought.user_id != user.id:
        return None
    return thought


async def next_open_after(
    session: AsyncSession, user: User, after: ParkedThought | None
) -> ParkedThought | None:
    """For reviewing one at a time: the next open thought in creation order."""
    stmt = select(ParkedThought).where(_open(user))
    if after is not None:
        stmt = stmt.where(ParkedThought.seq > after.seq)
    stmt = stmt.order_by(ParkedThought.seq).limit(1)
    return await session.scalar(stmt)


# --- the evening walk --------------------------------------------------------------
async def walk_thought_today(session: AsyncSession, user: User) -> ParkedThought | None:
    return await session.scalar(
        select(ParkedThought)
        .where(_open(user), ParkedThought.taken_on == local_today(user.timezone))
        .limit(1)
    )


async def take_for_walk(
    session: AsyncSession, user: User, instead_of: ParkedThought | None = None
) -> ParkedThought | None:
    """Take one open thought for today's walk: the oldest one, or — when the
    owner asks for another — the next one after ``instead_of`` (wrapping)."""
    today = local_today(user.timezone)
    candidates = await open_thoughts(session, user)
    if instead_of is not None:
        others = [t for t in candidates if t.id != instead_of.id]
        if not others:
            return None
        after = [t for t in others if t.seq > instead_of.seq]
        chosen = (after or others)[0]
        instead_of.taken_on = None
    else:
        current = next((t for t in candidates if t.taken_on == today), None)
        if current is not None:
            return current  # one thought per walk
        if not candidates:
            return None
        chosen = candidates[0]
    chosen.taken_on = today
    await session.flush()
    return chosen


# --- resolving ---------------------------------------------------------------------
async def resolve(
    session: AsyncSession, user: User, thought: ParkedThought, action: Action
) -> bool:
    """Apply the owner's choice. Returns True if this is the moment to offer
    (once) moving the thought to the session list."""
    if action == Action.LATER:
        thought.postponed_count += 1
        suggest = thought.postponed_count >= SUGGEST_SESSION_AFTER and not thought.session_suggested
        if suggest:
            thought.session_suggested = True
        await session.flush()
        return suggest
    if action == Action.SESSION:
        item = await add_item(session, user, thought.text, thought.message_id)
        thought.discussion_item_id = item.id
        thought.status = ThoughtStatus.TO_SESSION
    elif action == Action.DONE:
        thought.status = ThoughtStatus.DONE
    elif action == Action.DROP:
        thought.status = ThoughtStatus.DROPPED
    thought.resolved_at_utc = utcnow()
    await session.flush()
    return False
