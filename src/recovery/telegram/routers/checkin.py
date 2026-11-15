"""Morning / evening / ad-hoc check-ins on inline buttons.

FSM data while a check-in is active:
``checkin_id``, ``question`` (current question id), ``sel`` (pending
multi-select values) and ``prompt`` (message id of the current question).
The data lives in Postgres, so buttons keep working after a restart.
"""

import html
import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from recovery import battery, thoughts, winddown
from recovery.conversation.engine import (
    CheckinEngine,
    InvalidAnswer,
    disabled_features,
    flow_for,
    next_question,
)
from recovery.conversation.flows import Kind, Question, get_flow
from recovery.db.models import Checkin, ParkedThought, User
from recovery.domain import CheckinStatus, CheckinType, FlowVariant
from recovery.scheduler.prompts import cancel_reminder
from recovery.telegram.callbacks import AnswerCb, StartCb, ThoughtCb, short_id
from recovery.telegram.keyboards import (
    build,
    offer_keyboard,
    question_keyboard,
    resume_keyboard,
)
from recovery.texts import ru

logger = logging.getLogger(__name__)

OFFERS = {CheckinType.MORNING: ru.OFFER_MORNING, CheckinType.EVENING: ru.OFFER_EVENING}


class CheckinStates(StatesGroup):
    active = State()


async def send_offer(bot: Bot, chat_id: int, kind: CheckinType) -> None:
    """Scheduled prompt: a gentle "start?" message rather than a question
    thrown at the owner, so it never collides with another open dialog."""
    await bot.send_message(chat_id, OFFERS[kind], reply_markup=offer_keyboard(kind))


# --- core flow ------------------------------------------------------------------
async def _ask(
    bot: Bot,
    user: User,
    state: FSMContext,
    checkin: Checkin,
    q: Question,
    text: str | None = None,
) -> None:
    text = text or q.text + (ru.MULTI_HINT if q.kind == Kind.MULTI else "")
    msg = await bot.send_message(
        user.telegram_id, text, reply_markup=question_keyboard(q, short_id(checkin.id))
    )
    await state.set_state(CheckinStates.active)
    await state.update_data(
        checkin_id=str(checkin.id), question=q.id, sel=[], prompt=msg.message_id
    )


async def _advance(
    bot: Bot, user: User, state: FSMContext, engine: CheckinEngine, checkin: Checkin
) -> None:
    skip = disabled_features(user)
    while (question := next_question(checkin, skip_features=skip)) is not None:
        if question.kind == Kind.THOUGHT:
            thought = await thoughts.walk_thought_today(engine.session, user)
            if thought is None:  # nothing was taken on today's walk
                await engine.skip(checkin, question)
                continue
            text = f"{question.text}\n«{html.escape(thought.text)}»"
            await _ask(bot, user, state, checkin, question, text)
            return
        await _ask(bot, user, state, checkin, question)
        return
    await engine.finish(checkin)
    await state.clear()
    await bot.send_message(user.telegram_id, flow_for(checkin).outro)


async def _close_prompt(bot: Bot, chat_id: int, message_id: int | None, text: str | None) -> None:
    """Replace the answered question's keyboard with the chosen answer."""
    if message_id is None:
        return
    with suppress(TelegramBadRequest):
        if text is None:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=None
            )
        else:
            await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id, reply_markup=None
            )


def _answered_text(question: Question, label: str) -> str:
    return f"{question.text}\n<b>→ {html.escape(label)}</b>"


async def suggest_session(bot: Bot, user: User, thought: ParkedThought) -> None:
    """Third "пусть полежит": offer — once — to take it to the psychologist."""
    await bot.send_message(
        user.telegram_id,
        ru.THOUGHT_SUGGEST_SESSION.format(text=html.escape(thought.text)),
        reply_markup=build(
            [
                [
                    (ru.BTN_THOUGHT_SESSION, ThoughtCb(op="ssn", t=thought.id.hex).pack()),
                    (ru.BTN_KEEP, ThoughtCb(op="keep", t=thought.id.hex).pack()),
                ]
            ]
        ),
    )


async def begin(
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
    kind: CheckinType,
    variant: str | None = None,
    late_offer: bool = True,
) -> None:
    engine = CheckinEngine(session, user)
    name = ru.CHECKIN_NAME[kind]
    if kind != CheckinType.AD_HOC:
        existing = await engine.find_today(kind)
        if existing is not None and existing.status == CheckinStatus.COMPLETED:
            await bot.send_message(user.telegram_id, ru.ALREADY_DONE.format(name=name))
            return
        if existing is not None:
            await bot.send_message(
                user.telegram_id,
                ru.RESUME_PROMPT.format(name=name),
                reply_markup=resume_keyboard(kind),
            )
            return
    if kind == CheckinType.EVENING and late_offer and winddown.is_late(user):
        if winddown.is_night(user):
            # After midnight the evening is over: no review, only parking.
            await bot.send_message(user.telegram_id, ru.NIGHT_PARK_ONLY)
            return
        await bot.send_message(
            user.telegram_id,
            ru.LATE_EVENING_OFFER,
            reply_markup=build(
                [
                    [(ru.BTN_SHORT, StartCb(kind=kind, op="short").pack())],
                    [(ru.BTN_FULL, StartCb(kind=kind, op="full").pack())],
                    [(ru.BTN_NOT_NOW, StartCb(kind=kind, op="later").pack())],
                ]
            ),
        )
        return
    checkin = await engine.start(kind, variant)
    await bot.send_message(user.telegram_id, get_flow(kind, variant).intro)
    await _advance(bot, user, state, engine, checkin)


# --- handlers -------------------------------------------------------------------
async def cmd_morning(
    message: Message, bot: Bot, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await begin(bot, session, user, state, CheckinType.MORNING)


async def cmd_evening(
    message: Message, bot: Bot, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await begin(bot, session, user, state, CheckinType.EVENING)


async def cmd_now(
    message: Message, bot: Bot, session: AsyncSession, user: User, state: FSMContext
) -> None:
    await begin(bot, session, user, state, CheckinType.AD_HOC)


async def on_start_button(
    cb: CallbackQuery,
    callback_data: StartCb,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    await cb.answer()
    if cb.message is not None:
        await _close_prompt(bot, user.telegram_id, cb.message.message_id, None)
    kind = CheckinType(callback_data.kind)
    engine = CheckinEngine(session, user)
    if callback_data.op == "later":
        await cancel_reminder(session, user, kind)  # "not now" means no reminder later
        late = winddown.is_late(user)
        await bot.send_message(user.telegram_id, ru.LATE_NOT_NOW if late else ru.NOT_NOW)
        return
    if callback_data.op in ("short", "full"):
        variant = FlowVariant.SHORT if callback_data.op == "short" else None
        await begin(bot, session, user, state, kind, variant, late_offer=False)
        return
    if callback_data.op in ("resume", "restart") and kind != CheckinType.AD_HOC:
        existing = await engine.find_today(kind)
        if existing is not None and existing.status == CheckinStatus.IN_PROGRESS:
            if callback_data.op == "resume":
                await _advance(bot, user, state, engine, existing)
                return
            await engine.abandon(existing)
            checkin = await engine.start(kind, existing.flow_variant)
            await bot.send_message(user.telegram_id, flow_for(checkin).intro)
            await _advance(bot, user, state, engine, checkin)
            return
    await begin(bot, session, user, state, kind)


async def on_answer(
    cb: CallbackQuery,
    callback_data: AnswerCb,
    bot: Bot,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    checkin_id = data.get("checkin_id")
    engine = CheckinEngine(session, user)
    checkin = await engine.get(checkin_id) if checkin_id else None
    current = data.get("question")
    stale = (
        checkin is None
        or checkin.status != CheckinStatus.IN_PROGRESS
        or short_id(checkin.id) != callback_data.c
        or (callback_data.op != "fin" and current != callback_data.q)
    )
    if stale or checkin is None:
        await cb.answer(ru.STALE_QUESTION)
        if cb.message is not None:
            await _close_prompt(bot, user.telegram_id, cb.message.message_id, None)
        return

    question = flow_for(checkin).get(current or "")
    prompt = data.get("prompt")
    if callback_data.op == "fin" or question is None:
        await cb.answer()
        await _close_prompt(bot, user.telegram_id, prompt, None)
        await engine.finish(checkin)
        await state.clear()
        await bot.send_message(user.telegram_id, flow_for(checkin).outro)
        return

    if callback_data.op == "tog":
        selected: list[str] = list(data.get("sel", []))
        value, none = callback_data.v, question.none_option
        if value in selected:
            selected.remove(value)
        elif value == none:
            selected = [value]  # "ничего из этого" clears the rest
        else:
            selected = [v for v in selected if v != none] + [value]
        await state.update_data(sel=selected)
        await cb.answer()
        if cb.message is not None:
            with suppress(TelegramBadRequest):
                await cb.message.edit_reply_markup(  # type: ignore[union-attr]
                    reply_markup=question_keyboard(question, callback_data.c, selected)
                )
        return

    suggest: ParkedThought | None = None
    try:
        if callback_data.op == "skip":
            await engine.skip(checkin, question)
            label = ru.ANSWER_NOTHING if question.kind == Kind.PARK else ru.ANSWER_SKIPPED
        elif callback_data.op == "done":
            selected = list(data.get("sel", []))
            await engine.answer(checkin, question, selected)
            label = ", ".join(question.option_label(v) for v in selected) or (
                ru.ANSWER_NONE_SELECTED
            )
        else:
            suggest = await engine.answer(checkin, question, callback_data.v)
            label = (
                battery.bar(int(callback_data.v), user.battery_color, user.battery_reserve_pct)
                if question.kind == Kind.PERCENT
                else question.option_label(callback_data.v)
            )
    except InvalidAnswer:
        await cb.answer(ru.STALE_QUESTION)
        return

    await cb.answer()
    await _close_prompt(bot, user.telegram_id, prompt, _answered_text(question, label))
    if question.kind == Kind.PERCENT and battery.in_reserve(
        checkin.battery_pct, checkin.battery_reserve_pct
    ):
        # Exercise, step 4: a low charge is information, not a failure.
        await bot.send_message(user.telegram_id, ru.BATTERY_IN_RESERVE[checkin.checkin_type])
    if suggest is not None:
        await suggest_session(bot, user, suggest)
    await _advance(bot, user, state, engine, checkin)


async def on_text_in_checkin(
    message: Message, bot: Bot, session: AsyncSession, user: User, state: FSMContext
) -> None:
    data = await state.get_data()
    engine = CheckinEngine(session, user)
    checkin = await engine.get(data["checkin_id"]) if data.get("checkin_id") else None
    if checkin is None or checkin.status != CheckinStatus.IN_PROGRESS:
        await state.clear()
        raise SkipHandler  # not ours any more: let the free-text handler take it
    question = flow_for(checkin).get(data.get("question", ""))
    text = message.text or ""
    if question is not None and question.kind in (Kind.TEXT, Kind.PARK):
        try:
            await engine.answer(checkin, question, text)
        except InvalidAnswer:
            await message.answer(ru.NOTE_ADDED)  # blank text: nothing to store
            return
        label = (
            ru.ANSWER_PARKED.format(count=len(thoughts.split(text)))
            if question.kind == Kind.PARK
            else ru.ANSWER_SAVED
        )
        await _close_prompt(
            bot, user.telegram_id, data.get("prompt"), _answered_text(question, label)
        )
        await _advance(bot, user, state, engine, checkin)
        return
    # A free-form remark while a button question is open: keep it as a note.
    await engine.add_note(checkin, text)
    await message.answer(ru.NOTE_ADDED)


def make_router() -> Router:
    router = Router(name="checkin")
    router.message.register(cmd_morning, Command("morning"))
    router.message.register(cmd_evening, Command("evening"))
    router.message.register(cmd_now, Command("now"))
    router.callback_query.register(on_start_button, StartCb.filter())
    router.callback_query.register(on_answer, AnswerCb.filter())
    router.message.register(
        on_text_in_checkin, CheckinStates.active, F.text, ~F.text.startswith("/")
    )
    return router
