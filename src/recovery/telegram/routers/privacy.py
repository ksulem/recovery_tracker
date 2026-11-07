"""/privacy, /export_all and /delete_me (spec §12.7)."""

import uuid

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.db.models import DialogState, User
from recovery.reports.export import export_json
from recovery.telegram.callbacks import DeleteCb
from recovery.telegram.hooks import UserDeleted
from recovery.telegram.keyboards import build
from recovery.texts import ru


async def cmd_privacy(message: Message) -> None:
    await message.answer(ru.PRIVACY)


async def cmd_export(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    filename, body = await export_json(session, user)
    await bot.send_document(
        user.telegram_id, BufferedInputFile(body, filename=filename), caption=ru.EXPORT_CAPTION
    )


async def cmd_delete(message: Message) -> None:
    await message.answer(
        ru.DELETE_CONFIRM,
        reply_markup=build(
            [
                [(ru.BTN_DELETE_YES, DeleteCb(op="yes").pack())],
                [(ru.BTN_DELETE_NO, DeleteCb(op="no").pack())],
            ]
        ),
    )


async def on_delete(
    cb: CallbackQuery,
    callback_data: DeleteCb,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    user_deleted: UserDeleted,
) -> None:
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    if callback_data.op != "yes":
        await bot.send_message(user.telegram_id, ru.CANCELLED)
        return
    user_id: uuid.UUID = user.id
    telegram_id = user.telegram_id
    await state.clear()
    # FSM rows are keyed "<bot>:<chat>:<user>:…"; remove every one for this chat.
    await session.execute(delete(DialogState).where(DialogState.key.like(f"%:{telegram_id}:%")))
    await session.delete(user)  # everything else cascades
    await user_deleted(user_id)
    await bot.send_message(telegram_id, ru.DELETED)


def make_router() -> Router:
    router = Router(name="privacy")
    router.message.register(cmd_privacy, Command("privacy"))
    router.message.register(cmd_export, Command("export_all"))
    router.message.register(cmd_delete, Command("delete_me"))
    router.callback_query.register(on_delete, DeleteCb.filter())
    return router
