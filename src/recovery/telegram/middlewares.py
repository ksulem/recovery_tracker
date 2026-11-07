"""Dispatcher-level middlewares."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Chat, TelegramObject, User
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db import models

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class OwnerOnlyMiddleware(BaseMiddleware):
    """Drop every update that doesn't come from the owner in a private chat.

    Registered as an *outer* middleware on ``dp.update`` so it covers messages,
    button callbacks, voice notes and any update type added later — a handler
    can't forget the check. Strangers get no reply at all (spec §12.1).
    """

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user: User | None = data.get("event_from_user")
        chat: Chat | None = data.get("event_chat")
        if user is None or user.id != self.owner_id:
            logger.debug("dropped update from a non-owner")
            return None
        if chat is not None and chat.type != ChatType.PRIVATE:
            logger.debug("dropped update from a non-private chat")
            return None
        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    """One DB session (unit of work) per update, plus the owner's ``User`` row.

    Handlers get ``session`` and ``user``; the session is committed after a
    successful handler and rolled back if it raises.
    """

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], default_timezone: str
    ) -> None:
        self.session_factory = session_factory
        self.default_timezone = default_timezone

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user: User = data["event_from_user"]
        async with self.session_factory() as session:
            data["session"] = session
            data["user"] = await self._get_or_create_user(session, tg_user.id)
            result = await handler(event, data)
            await session.commit()
            return result

    async def _get_or_create_user(self, session: AsyncSession, telegram_id: int) -> models.User:
        """Race-free: updates that arrive before the first one has committed
        (e.g. messages queued while the bot was down) must not collide on
        ``uq_users_telegram_id``. A concurrent insert waits for the other
        transaction and then does nothing; the re-select sees its row."""
        query = select(models.User).where(models.User.telegram_id == telegram_id)
        user = await session.scalar(query)
        if user is None:
            await session.execute(
                insert(models.User)
                .values(telegram_id=telegram_id, timezone=self.default_timezone)
                .on_conflict_do_nothing(index_elements=[models.User.telegram_id])
            )
            user = (await session.scalars(query)).one()
        return user
