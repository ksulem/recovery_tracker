"""Crisis safety handling (spec §12, last paragraph).

If a message suggests immediate danger to oneself, the bot stops its normal
flow and shows a short message pointing to emergency services and trusted
people. It does not assess, diagnose or counsel.

A keyword detector is deliberately simple and errs towards showing the
message: a false positive costs one extra message ("если это не об этом —
можно просто продолжить"); a false negative costs much more. In M3 the LLM's
``safety_concern`` flag is added on top. Only the *fact* of the event is
recorded — never the text in logs.
"""

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import utcnow
from recovery.db import models
from recovery.domain import InputType, SafetySource
from recovery.texts import ru

logger = logging.getLogger(__name__)

_PATTERNS = [
    r"не\s+хо(чу|чется|тела?)\s+(больше\s+)?жить",
    r"не\s+могу\s+больше\s+жить",
    r"(нет|не\s+вижу)\s+смысла\s+жить",
    r"поконч\w*\s+с\s+собой",
    r"уб(ить|ью|ей)\s+себя",
    r"суицид",
    r"самоубийств",
    r"свести\s+сч[её]ты\s+с\s+жизнью",
    r"(хочу|хочется|хотелось\s+бы)\s+умереть",
    r"лучше\s+бы\s+(меня\s+не\s+было|я\s+не\s+(жил|родил)\w*)",
    r"(причинить|причиню|навредить|наврежу)\s+себе",
    r"(вскры\w*|пореза\w*|порежу)\s+(себе\s+)?вен",
    r"наглота\w*\s+таблет",
    r"выпилиться",
]
_DETECTOR = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)


def looks_like_crisis(text: str) -> bool:
    return bool(_DETECTOR.search(text.replace("ё", "е").replace("Ё", "Е")))


Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class SafetyMiddleware(BaseMiddleware):
    """Outer middleware on messages: runs before any handler or state filter,
    so it covers check-in answers, notes, /discuss and setup inputs alike."""

    def __init__(self, emergency_number: str) -> None:
        self.emergency_number = emergency_number

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)
        text = event.text or event.caption or ""
        if not text or text.startswith("/") or not looks_like_crisis(text):
            return await handler(event, data)

        session: AsyncSession = data["session"]
        user: models.User = data["user"]
        now = utcnow()
        # Keep the owner's own words in their diary, like any other note.
        session.add(
            models.Message(
                user_id=user.id,
                telegram_message_id=event.message_id,
                input_type=InputType.TEXT,
                text=text,
                tz_name=user.timezone,
                created_at_utc=now,
            )
        )
        session.add(
            models.SafetyEvent(
                user_id=user.id,
                source=SafetySource.KEYWORD,
                tz_name=user.timezone,
                created_at_utc=now,
            )
        )
        logger.info("safety response shown")  # the fact only, never the text
        await event.answer(ru.CRISIS.format(number=self.emergency_number))
        return None  # the normal flow stops here; a check-in stays resumable
