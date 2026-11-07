"""Free-form diary notes, the "to discuss" list and the catch-all handlers.

This router is included last: it only sees text that no check-in, setup or
session step consumed.
"""

import html
import uuid

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.types import Message as TgMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import utcnow
from recovery.db.models import DiscussionItem, Message, User
from recovery.domain import DiscussionStatus, InputType
from recovery.reports.digest import open_discussion_items
from recovery.telegram.callbacks import NoteCb, StartCb
from recovery.telegram.keyboards import build
from recovery.texts import ru


class DiscussStates(StatesGroup):
    waiting = State()


async def save_note(session: AsyncSession, user: User, message: TgMessage) -> Message:
    note = Message(
        user_id=user.id,
        telegram_message_id=message.message_id,
        input_type=InputType.TEXT,
        text=message.text,
        tz_name=user.timezone,
        created_at_utc=utcnow(),
    )
    session.add(note)
    await session.flush()
    return note


async def add_item(
    session: AsyncSession, user: User, text: str, message_id: uuid.UUID | None = None
) -> int:
    session.add(
        DiscussionItem(
            user_id=user.id,
            text=text.strip(),
            message_id=message_id,
            status=DiscussionStatus.OPEN,
            tz_name=user.timezone,
            created_at_utc=utcnow(),
        )
    )
    await session.flush()
    count = await session.scalar(
        select(func.count())
        .select_from(DiscussionItem)
        .where(DiscussionItem.user_id == user.id, DiscussionItem.status == DiscussionStatus.OPEN)
    )
    return int(count or 0)


async def on_free_text(message: TgMessage, session: AsyncSession, user: User) -> None:
    note = await save_note(session, user, message)
    keyboard = build(
        [
            [
                (ru.BTN_TO_SESSION, NoteCb(op="disc", m=note.id.hex).pack()),
                (ru.BTN_MARK_STATE, StartCb(kind="ad_hoc", op="go").pack()),
            ]
        ]
    )
    await message.answer(ru.NOTE_SAVED, reply_markup=keyboard)


async def on_note_to_session(
    cb: CallbackQuery, callback_data: NoteCb, session: AsyncSession, user: User
) -> None:
    note = await session.get(Message, uuid.UUID(hex=callback_data.m))
    if note is None or note.user_id != user.id or not note.text:
        await cb.answer(ru.STALE_QUESTION)
        return
    exists = await session.scalar(
        select(DiscussionItem.id).where(DiscussionItem.message_id == note.id)
    )
    if exists:
        await cb.answer(ru.DISCUSS_ALREADY)
        return
    count = await add_item(session, user, note.text, note.id)
    await cb.answer(ru.DISCUSS_ADDED.format(count=count))
    if cb.message is not None:
        await cb.message.answer(ru.DISCUSS_ADDED.format(count=count))


async def cmd_discuss(
    message: TgMessage,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    if command.args and command.args.strip():
        count = await add_item(session, user, command.args)
        await message.answer(ru.DISCUSS_ADDED.format(count=count))
        return
    items = await open_discussion_items(session, user)
    if items:
        lines = [ru.DISCUSS_LIST_TITLE] + [f"• {html.escape(i.text)}" for i in items]
        await message.answer("\n".join(lines))
    else:
        await message.answer(ru.DISCUSS_EMPTY)
    await state.set_state(DiscussStates.waiting)
    await message.answer(ru.DISCUSS_PROMPT)


async def on_discuss_text(
    message: TgMessage, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await state.set_state(None)
    count = await add_item(session, user, message.text or "")
    await message.answer(ru.DISCUSS_ADDED.format(count=count))


async def cmd_cancel(message: TgMessage, state: FSMContext) -> None:
    await state.set_state(None)
    await message.answer(ru.CANCELLED)


async def on_unknown_command(message: TgMessage) -> None:
    await message.answer(ru.UNKNOWN_COMMAND.format(help=ru.HELP))


def make_router() -> Router:
    router = Router(name="notes")
    router.message.register(cmd_discuss, Command("discuss"))
    router.message.register(cmd_cancel, Command("cancel"))
    router.callback_query.register(on_note_to_session, NoteCb.filter(F.op == "disc"))
    router.message.register(on_discuss_text, DiscussStates.waiting, F.text, ~F.text.startswith("/"))
    router.message.register(on_unknown_command, F.text.startswith("/"))
    router.message.register(on_free_text, F.text)
    return router
