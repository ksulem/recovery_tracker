"""Parked thoughts: /later, reviewing them one at a time, the pre-walk offer.

The walk is also for unloading the brain after work, so the offer is light:
one thought, only on request, nothing to decide during the walk. What to do
with it is asked at the end of the evening check-in.
"""

import html

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from recovery import thoughts, winddown
from recovery.db.models import ParkedThought, User
from recovery.domain import ThoughtStatus
from recovery.reports.digest import fmt_date
from recovery.telegram.callbacks import ThoughtCb
from recovery.telegram.keyboards import build
from recovery.telegram.routers.checkin import suggest_session
from recovery.texts import ru

LIST_LIMIT = 15


class ThoughtStates(StatesGroup):
    waiting = State()


def _parked_reply(user: User, count: int) -> str:
    if winddown.is_late(user):
        return ru.LATE_PARKED
    return ru.PARKED if count == 1 else ru.PARKED_MANY.format(count=count)


# --- the pre-walk offer (sent by the scheduler) ------------------------------------
async def send_pre_walk(bot: Bot, session: AsyncSession, user: User) -> bool:
    """Only if something is parked, and never in late mode."""
    if winddown.is_late(user):
        return False
    count = await thoughts.count_open(session, user)
    if count == 0:
        return False
    await bot.send_message(
        user.telegram_id,
        ru.PRE_WALK.format(count=count),
        reply_markup=build(
            [
                [
                    (ru.BTN_TAKE_ONE, ThoughtCb(op="take").pack()),
                    (ru.BTN_JUST_WALK, ThoughtCb(op="walk").pack()),
                ]
            ]
        ),
    )
    return True


def _walk_text(thought: ParkedThought) -> str:
    return ru.WALK_THOUGHT.format(text=html.escape(thought.text))


def _walk_keyboard(thought: ParkedThought) -> InlineKeyboardMarkup:
    return build([[(ru.BTN_ANOTHER, ThoughtCb(op="other", t=thought.id.hex).pack())]])


# --- reviewing one at a time -------------------------------------------------------
def _review_text(thought: ParkedThought) -> str:
    return ru.THOUGHT_REVIEW.format(
        text=html.escape(thought.text), date=fmt_date(thought.local_date, with_weekday=False)
    )


def _review_keyboard(thought: ParkedThought) -> InlineKeyboardMarkup:
    def act(action: thoughts.Action) -> str:
        return ThoughtCb(op="act", t=thought.id.hex, a=action).pack()

    return build(
        [
            [
                (ru.BTN_THOUGHT_DONE, act(thoughts.Action.DONE)),
                (ru.BTN_THOUGHT_LATER, act(thoughts.Action.LATER)),
            ],
            [
                (ru.BTN_THOUGHT_SESSION, act(thoughts.Action.SESSION)),
                (ru.BTN_THOUGHT_DROP, act(thoughts.Action.DROP)),
            ],
        ]
    )


async def _show(cb: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None) -> None:
    if cb.message is None:
        return
    try:
        await cb.message.edit_text(text, reply_markup=keyboard)  # type: ignore[union-attr]
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=keyboard)


# --- handlers ----------------------------------------------------------------------
async def cmd_later(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    state: FSMContext,
) -> None:
    if command.args and thoughts.split(command.args):
        parked = await thoughts.park(session, user, command.args)
        await message.answer(_parked_reply(user, len(parked)))
        return
    await state.set_state(ThoughtStates.waiting)
    if winddown.is_late(user):
        # Late: no list to read through, just a place to put the thought.
        await message.answer(ru.LATER_PROMPT_LATE)
        return
    open_ = await thoughts.open_thoughts(session, user, limit=LIST_LIMIT)
    if not open_:
        await message.answer(f"{ru.LATER_EMPTY}\n\n{ru.LATER_PROMPT}")
        return
    total = await thoughts.count_open(session, user)
    lines = [ru.LATER_LIST_TITLE.format(count=total)]
    lines += [f"{n}. {html.escape(t.text)}" for n, t in enumerate(open_, start=1)]
    if total > len(open_):
        lines.append(f"… и ещё {total - len(open_)}")
    lines += ["", ru.LATER_PROMPT]
    await message.answer(
        "\n".join(lines),
        reply_markup=build([[(ru.BTN_REVIEW, ThoughtCb(op="review").pack())]]),
    )


async def on_later_text(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    parked = await thoughts.park(session, user, message.text or "")
    if not parked:
        await message.answer(ru.LATER_PROMPT)
        return
    await state.set_state(None)
    await message.answer(_parked_reply(user, len(parked)))


async def on_thought_button(
    cb: CallbackQuery,
    callback_data: ThoughtCb,
    bot: Bot,
    session: AsyncSession,
    user: User,
) -> None:
    op = callback_data.op
    current = await thoughts.get(session, user, callback_data.t) if callback_data.t else None

    if op == "walk":
        await cb.answer()
        await _show(cb, ru.JUST_WALK, None)
        return
    if op in ("take", "other"):
        chosen = await thoughts.take_for_walk(session, user, instead_of=current)
        if chosen is None:
            await cb.answer(ru.NO_OTHER_THOUGHTS if op == "other" else ru.LATER_EMPTY)
            return
        await cb.answer()
        await _show(cb, _walk_text(chosen), _walk_keyboard(chosen))
        return
    if op == "review":
        await cb.answer()
        first = await thoughts.next_open_after(session, user, None)
        if first is None:
            await _show(cb, ru.LATER_EMPTY, None)
            return
        await _show(cb, _review_text(first), _review_keyboard(first))
        return
    if current is None or current.status != ThoughtStatus.OPEN:
        await cb.answer(ru.STALE_QUESTION)
        return
    if op == "act":
        suggest = await thoughts.resolve(session, user, current, thoughts.Action(callback_data.a))
        await cb.answer(ru.THOUGHT_ACK[callback_data.a])
        following = await thoughts.next_open_after(session, user, current)
        if following is None:
            await _show(cb, ru.REVIEW_DONE, None)
        else:
            await _show(cb, _review_text(following), _review_keyboard(following))
        if suggest:
            await suggest_session(bot, user, current)
        return
    if op == "ssn":
        await thoughts.resolve(session, user, current, thoughts.Action.SESSION)
        await cb.answer()
        await _show(cb, ru.THOUGHT_MOVED_TO_SESSION, None)
        return
    if op == "keep":
        await cb.answer()
        await _show(cb, ru.THOUGHT_KEPT, None)


def make_router() -> Router:
    router = Router(name="thoughts")
    router.message.register(cmd_later, Command("later"))
    router.callback_query.register(on_thought_button, ThoughtCb.filter())
    router.message.register(on_later_text, ThoughtStates.waiting, F.text, ~F.text.startswith("/"))
    return router
