from collections.abc import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from recovery.conversation.flows import Kind, Question
from recovery.telegram.callbacks import AnswerCb, StartCb
from recovery.texts import ru

Row = Sequence[tuple[str, str]]


def build(rows: Sequence[Row]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
            for row in rows
            if row
        ]
    )


def chunk(items: Sequence[tuple[str, str]], size: int) -> list[list[tuple[str, str]]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def question_keyboard(
    question: Question, cid: str, selected: Sequence[str] = ()
) -> InlineKeyboardMarkup:
    def cb(op: str, value: str = "") -> str:
        return AnswerCb(c=cid, q=question.id, op=op, v=value).pack()

    rows: list[list[tuple[str, str]]] = []
    if question.kind == Kind.SCALE_10:
        buttons = [(str(n), cb("set", str(n))) for n in range(11)]
        rows = [buttons[:6], buttons[6:]]
    elif question.kind == Kind.PERCENT:
        buttons = [(f"{n}%", cb("set", str(n))) for n in range(0, 101, 10)]
        rows = [buttons[:6], buttons[6:]]
    elif question.kind in (Kind.SCALE_5, Kind.CHOICE, Kind.GATE):
        buttons = [(o.label, cb("set", o.value)) for o in question.options]
        short = all(len(o.label) <= 12 for o in question.options)
        rows = chunk(buttons, 3) if short else chunk(buttons, 1)
    elif question.kind == Kind.MULTI:
        rows = [
            [(("✓ " if o.value in selected else "") + o.label, cb("tog", o.value))]
            for o in question.options
        ]
        rows.append([(ru.BTN_DONE, cb("done"))])
    rows.append([(ru.BTN_SKIP, cb("skip")), (ru.BTN_FINISH, cb("fin"))])
    return build(rows)


def offer_keyboard(kind: str) -> InlineKeyboardMarkup:
    return build(
        [
            [
                (ru.BTN_START, StartCb(kind=kind, op="go").pack()),
                (ru.BTN_NOT_NOW, StartCb(kind=kind, op="later").pack()),
            ]
        ]
    )


def resume_keyboard(kind: str) -> InlineKeyboardMarkup:
    return build(
        [
            [(ru.BTN_CONTINUE, StartCb(kind=kind, op="resume").pack())],
            [(ru.BTN_RESTART, StartCb(kind=kind, op="restart").pack())],
            [(ru.BTN_NOT_NOW, StartCb(kind=kind, op="later").pack())],
        ]
    )
