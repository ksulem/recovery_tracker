from datetime import date
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.conversation import flows
from recovery.conversation.engine import _validate, disabled_features
from recovery.conversation.flows import Flow, Kind, Option, Question, pick_next
from recovery.db.models import User
from recovery.domain import CheckinType
from recovery.texts import ru

ITEMS = (Option("a", "Альфа"), Option("b", "Бета"), Option("c", "Гамма"), Option("none", "Ничего"))

FLOW = Flow(
    checkin_type=CheckinType.EVENING,
    intro="",
    outro="",
    questions=(
        Question("pick", "Что было?", Kind.MULTI, options=ITEMS, none_option="none", max_items=2),
        Question(
            "rate", "{item} — как?", Kind.CHOICE, options=(Option("both", "Оба"),), each="pick"
        ),
        Question(
            "gave",
            "{item}: что дало?",
            Kind.CHOICE,
            options=(Option("x", "X"),),
            each="pick",
            when=("rate.{item}", frozenset({"both"})),
        ),
        Question(
            "why", "{item}: по желанию?", Kind.CHOICE, options=(), each="pick", items=frozenset("b")
        ),
        Question("press", "Давление?", Kind.CHOICE, options=(), every_days=2, feature="hyp"),
        Question("end", "Конец", Kind.TEXT),
    ),
)


def ids(status: dict[str, str], answers: dict[str, Any], **kw: Any) -> list[str]:
    """All questions the engine would ask in order, answering 'answered' to each."""
    status, out = dict(status), []
    while (q := pick_next(FLOW, status, answers, **kw)) is not None:
        out.append(q.id)
        status[q.id] = "answered"
    return out


def test_templates_expand_per_item_in_selection_order() -> None:
    answers = {"pick": ["b", "a"], "rate.b": "both", "rate.a": "other"}
    assert ids({"pick": "answered"}, answers, day=date(2026, 11, 16)) == [
        "rate.b",
        "gave.b",  # only for "both"
        "why.b",  # only for item b
        "rate.a",
        "press",
        "end",
    ]


def test_at_most_max_items_and_none_is_not_an_item() -> None:
    answers = {"pick": ["a", "b", "c"]}
    asked = ids({"pick": "answered"}, answers, skip_features={"hyp"})
    assert [i for i in asked if i.startswith("rate.")] == ["rate.a", "rate.b"]
    assert ids({"pick": "answered"}, {"pick": ["none"]}, skip_features={"hyp"}) == ["end"]


def test_every_other_day_and_feature_switch() -> None:
    status = {"pick": "skipped"}
    even, odd = date(2026, 11, 16), date(2026, 11, 17)
    assert even.toordinal() % 2 == 0
    assert "press" in ids(status, {}, day=even)
    assert "press" not in ids(status, {}, day=odd)
    assert "press" not in ids(status, {}, day=even, skip_features={"hyp"})


def test_instances_are_resolvable_by_id() -> None:
    q = FLOW.get("gave.a")
    assert q is not None and q.item == "a" and q.text == "Альфа: что дало?"
    assert q.when == ("rate.a", frozenset({"both"}))
    assert FLOW.get("gave.zzz") is None  # unknown item
    assert FLOW.get("rate") is None  # a bare template is not a question


def test_none_option_excludes_the_rest() -> None:
    pick = FLOW.get("pick")
    assert pick is not None
    assert _validate(pick, ["a", "none"]) == ["none"]
    assert _validate(pick, ["b", "a", "b"]) == ["b", "a"]


# --- optional question sets in /settings ----------------------------------------------
async def test_extra_questions_toggle(
    make_client: Any, db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(flows.FEATURE_SETS, "hyp", ("Гипотезы", "вечерние вопросы"))
    client = make_client()
    await client.send("/settings")
    await client.click_button(ru.BTN_SET_EXTRA)
    assert "<b>Гипотезы</b> — вечерние вопросы" in client.last_text()
    await client.click_button("✅ Гипотезы")
    async with db() as s:
        user = await s.scalar(select(User))
    assert user is not None and user.disabled_features == ["hyp"]
    assert "hyp" in disabled_features(user)
    await client.click_button("⬜ Гипотезы")
    async with db() as s:
        user = await s.scalar(select(User))
    assert user is not None and user.disabled_features == []
