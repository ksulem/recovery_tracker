"""Test doubles for driving the real dispatcher without Telegram.

``FakeSession`` records every Bot API call and returns plausible results, so
handlers run exactly as in production. ``TgClient`` builds updates for the
owner (or a stranger) and feeds them through ``dp.feed_update``.
"""

from __future__ import annotations

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
    WebhookInfo,
)

OWNER_ID = 1001
STRANGER_ID = 2002


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self._ids = itertools.count(10_000)
        self.webhook_url = ""

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        self.requests.append(method)
        returning = method.__returning__
        if returning is WebhookInfo:
            return WebhookInfo(  # type: ignore[return-value]
                url=self.webhook_url, has_custom_certificate=False, pending_update_count=0
            )
        if returning is User:
            return User(id=42, is_bot=True, first_name="Bot", username="test_bot")  # type: ignore[return-value]
        if returning in (Message, Message | bool):
            chat_id = getattr(method, "chat_id", None) or OWNER_ID
            msg = Message(
                message_id=getattr(method, "message_id", None) or next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=int(chat_id), type="private"),
                text=getattr(method, "text", None),
                reply_markup=getattr(method, "reply_markup", None),
            )
            return msg.as_(bot)  # type: ignore[return-value]
        return True  # type: ignore[return-value]

    async def close(self) -> None:
        return None

    async def stream_content(  # type: ignore[override]
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b""


class TgClient:
    def __init__(self, dp: Dispatcher, user_id: int = OWNER_ID) -> None:
        self.dp = dp
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.user = User(id=user_id, is_bot=False, first_name="Owner")
        self.chat = Chat(id=user_id, type="private")
        self._update_ids = itertools.count(1)
        self._message_ids = itertools.count(1)

    # --- sending updates ---------------------------------------------------
    async def send(self, text: str) -> None:
        message = Message(
            message_id=next(self._message_ids),
            date=datetime.now(UTC),
            chat=self.chat,
            from_user=self.user,
            text=text,
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=next(self._update_ids), message=message)
        )

    async def click(self, data: str, message: Message | None = None) -> None:
        source = message or Message(
            message_id=next(self._message_ids),
            date=datetime.now(UTC),
            chat=self.chat,
            text="(button message)",
        )
        query = CallbackQuery(
            id=str(next(self._update_ids)),
            from_user=self.user,
            chat_instance="ci",
            message=source,
            data=data,
        )
        await self.dp.feed_update(
            self.bot, Update(update_id=next(self._update_ids), callback_query=query)
        )

    async def click_button(self, label_part: str) -> None:
        """Click the first button whose label contains ``label_part`` in the
        most recent message that has an inline keyboard."""
        msg = self.last_with_keyboard()
        assert msg is not None, "no message with a keyboard was sent"
        markup = msg.reply_markup
        assert isinstance(markup, InlineKeyboardMarkup)
        for row in markup.inline_keyboard:
            for button in row:
                if label_part in button.text:
                    assert button.callback_data is not None
                    sent = Message(
                        message_id=getattr(msg, "message_id", None) or 1,
                        date=datetime.now(UTC),
                        chat=self.chat,
                        text=getattr(msg, "text", None),
                        reply_markup=markup,
                    )
                    await self.click(button.callback_data, sent)
                    return
        labels = [b.text for row in markup.inline_keyboard for b in row]
        raise AssertionError(f"no button containing {label_part!r}; have {labels}")

    # --- inspecting what the bot sent ----------------------------------------
    def texts(self) -> list[str]:
        return [r.text for r in self.session.requests if getattr(r, "text", None)]  # type: ignore[attr-defined]

    def last_text(self) -> str:
        texts = self.texts()
        assert texts, "bot sent nothing"
        return texts[-1]

    def last_with_keyboard(self) -> Any:
        for r in reversed(self.session.requests):
            if isinstance(getattr(r, "reply_markup", None), InlineKeyboardMarkup):
                return r
        return None

    def documents(self) -> list[Any]:
        return [r for r in self.session.requests if type(r).__name__ == "SendDocument"]

    def clear(self) -> None:
        self.session.requests.clear()
