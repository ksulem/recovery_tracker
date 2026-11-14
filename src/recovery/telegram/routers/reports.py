"""/week export and the psychologist session brief (v0: facts only)."""

import html
import uuid
from datetime import timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import local_today, utcnow
from recovery.db.models import DiscussionItem, TherapySession, User
from recovery.domain import DiscussionStatus
from recovery.reports.digest import (
    coverage_line,
    fmt_period,
    last_session,
    open_discussion_items,
    reserve_count_line,
    session_brief,
    week_markdown,
)
from recovery.telegram.callbacks import SessionCb
from recovery.telegram.keyboards import build
from recovery.texts import ru

MAX_ITEMS_IN_MESSAGE = 20
MAX_ITEM_CHARS = 300


class SessionStates(StatesGroup):
    note = State()


def _clip(text: str) -> str:
    return text if len(text) <= MAX_ITEM_CHARS else text[: MAX_ITEM_CHARS - 1] + "…"


async def send_session_brief(bot: Bot, session: AsyncSession, user: User) -> None:
    """Used by /session and by the scheduler before the session."""
    brief = await session_brief(session, user)
    lines = [
        ru.SESSION_TITLE.format(period=fmt_period(brief.start, brief.end)),
        coverage_line(brief.digest),
        *([reserve] if (reserve := reserve_count_line(brief.digest)) else []),
        "",
    ]
    if brief.items:
        lines.append(ru.SESSION_ITEMS_TITLE)
        lines += [f"• {html.escape(_clip(i.text))}" for i in brief.items[:MAX_ITEMS_IN_MESSAGE]]
        if len(brief.items) > MAX_ITEMS_IN_MESSAGE:
            lines.append(f"… и ещё {len(brief.items) - MAX_ITEMS_IN_MESSAGE} — в файле")
    else:
        lines.append(ru.SESSION_NO_ITEMS)
    if brief.previous_note:
        lines += ["", ru.SESSION_PREVIOUS_NOTE.format(note=html.escape(_clip(brief.previous_note)))]
    lines += ["", ru.SESSION_FILE_HINT]
    keyboard = build(
        [
            [
                (ru.BTN_SESSION_HELD, SessionCb(op="held").pack()),
                (ru.BTN_CLOSE, SessionCb(op="close").pack()),
            ]
        ]
    )
    await bot.send_message(user.telegram_id, "\n".join(lines), reply_markup=keyboard)
    await bot.send_document(
        user.telegram_id,
        BufferedInputFile(brief.markdown.encode(), filename=brief.filename),
    )


async def cmd_week(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    filename, text = await week_markdown(session, user)
    end = local_today(user.timezone)
    await bot.send_document(
        user.telegram_id,
        BufferedInputFile(text.encode(), filename=filename),
        caption=f"Неделя {fmt_period(end - timedelta(days=6), end)}",
    )


async def cmd_session(message: Message, bot: Bot, session: AsyncSession, user: User) -> None:
    await send_session_brief(bot, session, user)


async def _ask_note(bot: Bot, user: User, state: FSMContext) -> None:
    await state.set_state(SessionStates.note)
    await bot.send_message(
        user.telegram_id,
        ru.SESSION_NOTE_PROMPT,
        reply_markup=build([[(ru.BTN_SKIP, SessionCb(op="skipnote").pack())]]),
    )


async def on_session_button(
    cb: CallbackQuery,
    callback_data: SessionCb,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    op = callback_data.op
    if op == "close":
        return
    if op == "held":
        today = local_today(user.timezone)
        previous = await last_session(session, user)
        if previous is not None and previous.session_date == today:
            held = previous  # pressed twice: one session per day
        else:
            held = TherapySession(
                user_id=user.id,
                session_date=today,
                brief_period_start=previous.session_date if previous else today - timedelta(6),
                brief_period_end=today,
                tz_name=user.timezone,
                created_at_utc=utcnow(),
            )
            session.add(held)
            await session.flush()
        await state.update_data(therapy_session_id=str(held.id))
        if await open_discussion_items(session, user):
            await bot.send_message(
                user.telegram_id,
                ru.SESSION_ITEMS_QUESTION,
                reply_markup=build(
                    [
                        [(ru.BTN_ALL_DISCUSSED, SessionCb(op="all").pack())],
                        [(ru.BTN_KEEP_OPEN, SessionCb(op="keep").pack())],
                    ]
                ),
            )
            return
        await _ask_note(bot, user, state)
        return
    if op == "all":
        raw_id = (await state.get_data()).get("therapy_session_id")
        session_id = uuid.UUID(raw_id) if raw_id else None
        await session.execute(
            update(DiscussionItem)
            .where(
                DiscussionItem.user_id == user.id,
                DiscussionItem.status == DiscussionStatus.OPEN,
            )
            .values(
                status=DiscussionStatus.DISCUSSED,
                session_id=session_id,
                resolved_at_utc=utcnow(),
            )
        )
        await _ask_note(bot, user, state)
        return
    if op == "keep":
        await _ask_note(bot, user, state)
        return
    if op == "skipnote":
        await state.set_state(None)
        await bot.send_message(user.telegram_id, ru.SESSION_SAVED)


async def on_session_note(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    raw_id = (await state.get_data()).get("therapy_session_id")
    held = await session.get(TherapySession, uuid.UUID(raw_id)) if raw_id else None
    if held is not None and held.user_id == user.id:
        text = (message.text or "").strip()
        held.post_session_note = (
            f"{held.post_session_note}\n{text}" if held.post_session_note else text
        )
    await state.set_state(None)
    await message.answer(ru.SESSION_SAVED)


def make_router() -> Router:
    router = Router(name="reports")
    router.message.register(cmd_week, Command("week"))
    router.message.register(cmd_session, Command("session"))
    router.callback_query.register(on_session_button, SessionCb.filter())
    router.message.register(on_session_note, SessionStates.note, F.text, ~F.text.startswith("/"))
    return router
