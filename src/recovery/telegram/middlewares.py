"""Dispatcher-level middlewares."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Chat, TelegramObject, User

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
