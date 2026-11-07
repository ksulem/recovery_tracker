from datetime import UTC, datetime
from typing import Any

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.battery import bar, in_reserve
from recovery.db.models import Checkin, User
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]


async def _setup_user(client: Any, db: Factory, **fields: Any) -> None:
    await client.send("/ping")  # creates the user row
    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        for key, value in fields.items():
            setattr(user, key, value)
    client.clear()


async def _user(db: Factory) -> User:
    async with db() as s:
        user = await s.scalar(select(User))
    assert user is not None
    return user


async def _checkins(db: Factory) -> list[Checkin]:
    async with db() as s:
        return list((await s.scalars(select(Checkin).order_by(Checkin.created_at_utc))).all())


# --- rendering ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((40, "green", 30), "🟩🟩🟩|🟩⬜⬜⬜⬜⬜⬜ 40%"),
        ((20, "purple", 30), "🟪🟪⬜|⬜⬜⬜⬜⬜⬜⬜ 20%"),
        ((60, "blue", None), "🟦🟦🟦🟦🟦🟦⬜⬜⬜⬜ 60%"),
        ((0, "red", None), "⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0%"),
        ((100, "orange", 0), "🟧🟧🟧🟧🟧🟧🟧🟧🟧🟧 100%"),
    ],
)
def test_bar(args: tuple[int, str, int | None], expected: str) -> None:
    assert bar(*args) == expected


def test_in_reserve() -> None:
    assert in_reserve(30, 30)
    assert in_reserve(10, 30)
    assert not in_reserve(40, 30)
    assert not in_reserve(10, None)  # no reserve set: never "in reserve"
    assert not in_reserve(None, 30)


# --- check-ins -----------------------------------------------------------------------
async def test_low_morning_gets_a_gentle_note(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup_user(client, db, battery_reserve_pct=30, battery_color="purple")
    await client.send("/morning")
    await client.click_button("Плохо")
    assert client.last_text() == ru.Q_BATTERY_MORNING
    await client.click_button("30%")

    texts = client.texts()
    assert any("→ 🟪🟪🟪|⬜⬜⬜⬜⬜⬜⬜ 30%" in t for t in texts)  # answer shown as a bar
    assert ru.BATTERY_IN_RESERVE["morning"] in texts
    assert client.last_text() == ru.Q_PHYSICAL_NOW  # the check-in simply continues
    [c] = await _checkins(db)
    assert (c.battery_pct, c.battery_reserve_pct) == (30, 30)


async def test_no_note_above_the_reserve_or_without_one(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup_user(client, db, battery_reserve_pct=30)
    await client.send("/now")
    await client.click_button("50%")
    assert not any(t in client.texts() for t in ru.BATTERY_IN_RESERVE.values())

    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        user.battery_reserve_pct = None
    await client.send("/now")
    await client.click_button("0%")
    assert not any(t in client.texts() for t in ru.BATTERY_IN_RESERVE.values())
    assert [c.battery_reserve_pct for c in await _checkins(db)] == [30, None]


async def test_evening_and_now_notes(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup_user(client, db, battery_reserve_pct=40)
    await client.send("/evening")
    await client.click_button("Хуже")
    await client.click_button("20%")
    assert ru.BATTERY_IN_RESERVE["evening"] in client.texts()
    await client.send("/now")
    await client.click_button("10%")
    assert ru.BATTERY_IN_RESERVE["ad_hoc"] in client.texts()


async def test_battery_question_can_be_turned_off(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup_user(client, db, battery_enabled=False)
    await client.send("/morning")
    await client.click_button("Хорошо")
    assert client.last_text() == ru.Q_PHYSICAL_NOW
    await client.send("/evening")
    await client.click_button("Так же")
    assert client.last_text().startswith(ru.Q_DRAINS)
    await client.send("/now")
    assert client.last_text() == ru.Q_OVERALL_NOW
    assert all("battery" not in c.question_status for c in await _checkins(db))


async def test_only_whole_tens_are_accepted(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    await client.click_button("Хорошо")
    [c] = await _checkins(db)
    await client.click(f"qa:{c.id.hex[:8]}:battery:set:35")  # a forged button
    assert client.last_text() == ru.STALE_QUESTION
    [c] = await _checkins(db)
    assert c.battery_pct is None


# --- settings ------------------------------------------------------------------------
async def test_battery_settings(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/settings")
    assert "Батарейка: включена, резерв — не задан." in client.last_text()
    await client.click_button(ru.BTN_SET_BATTERY)
    assert client.last_text().startswith("<b>Батарейка</b>")

    await client.click_button(ru.BTN_BATTERY_RESERVE)
    assert client.last_text() == ru.ASK_BATTERY_RESERVE
    await client.click_button("30%")
    assert "Резерв: 30%" in client.last_text()

    await client.click_button(ru.BTN_BATTERY_COLOR)
    await client.click_button("фиолетовый")
    assert "🟪🟪🟪|🟪🟪🟪⬜⬜⬜⬜ 60%" in client.last_text()  # the example bar

    await client.click_button(ru.BTN_BATTERY_ON)
    assert ru.BATTERY_OFF in client.last_text()
    user = await _user(db)
    assert (user.battery_reserve_pct, user.battery_color, user.battery_enabled) == (
        30,
        "purple",
        False,
    )

    await client.click_button(ru.BTN_BACK)
    assert "Батарейка: выключена." in client.last_text()

    await client.click_button(ru.BTN_SET_BATTERY)
    await client.click_button(ru.BTN_BATTERY_RESERVE)
    await client.click_button(ru.BTN_NO_RESERVE)
    assert (await _user(db)).battery_reserve_pct is None


# --- reports -------------------------------------------------------------------------
async def test_reports_count_mornings_in_reserve(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup_user(client, db, battery_reserve_pct=30)
    for day, charge in ((3, "20%"), (4, "50%"), (5, "30%")):
        with time_machine.travel(datetime(2026, 11, day, 7, tzinfo=UTC), tick=False):
            await client.send("/morning")
            await client.click_button("Средне")
            await client.click_button(charge)
            await client.click_button(ru.BTN_FINISH)
    # Changing the reserve later must not rewrite earlier days.
    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        user.battery_reserve_pct = 50

    with time_machine.travel(datetime(2026, 11, 6, 12, tzinfo=UTC), tick=False):
        await client.send("/week")
        week = client.documents()[-1].document.data.decode()
        await client.send("/session")
        summary = client.texts()[-1]
        brief = client.documents()[-1].document.data.decode()

    count = ru.BATTERY_RESERVE_COUNT.format(count=2, total=3)
    for text in (week, summary, brief):
        assert count in text
    assert "- Вт, 3 ноября 2026: утро 20% (на резерве)" in week
    assert "- Ср, 4 ноября 2026: утро 50%\n" in week
    assert "заряд: 30% (на резерве)" in brief
    assert "средн" not in week.replace("после сна: средне", "").lower()  # no averages
