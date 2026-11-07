from datetime import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import User
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]


async def _user(db: Factory) -> User:
    async with db() as s:
        user = await s.scalar(select(User))
    assert user is not None
    return user


class Spy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, time, time]] = []

    async def __call__(self, user: User) -> None:
        self.calls.append((user.timezone, user.morning_time, user.evening_time))


async def test_onboarding_with_buttons(make_client: Any, db: Factory) -> None:
    spy = Spy()
    client = make_client(settings_changed=spy)
    await client.send("/start")
    assert ru.WELCOME in client.texts()
    assert client.last_text() == ru.ASK_TIMEZONE

    await client.click_button("Тбилиси")
    assert client.last_text() == ru.ASK_MORNING_TIME
    await client.click_button("09:00")
    await client.click_button("21:30")
    assert client.last_text() == ru.ASK_SESSION_DAY
    await client.click_button("Ср")
    assert client.last_text() == ru.ASK_SESSION_TIME
    await client.click_button("18:00")

    done = client.last_text()
    assert done.startswith("Готово")
    assert "по средам в 18:00" in done
    assert "/morning" in done

    user = await _user(db)
    assert user.onboarding_completed
    assert user.timezone == "Asia/Tbilisi"
    assert (user.morning_time, user.evening_time) == (time(9), time(21, 30))
    assert (user.session_weekday, user.session_time) == (2, time(18))
    assert spy.calls == [("Asia/Tbilisi", time(9), time(21, 30))]  # scheduler told once


async def test_onboarding_with_typed_values(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/start")
    await client.click_button(ru.BTN_OTHER)
    await client.send("Тбилиси")  # not an IANA name
    assert client.last_text() == ru.TIMEZONE_BAD
    await client.send("+4")
    await client.click_button(ru.BTN_OTHER)
    await client.send("25:00")
    assert client.last_text() == ru.TIME_BAD
    await client.send("8.15")
    await client.click_button("21:00")
    await client.click_button(ru.BTN_NO_SESSION)
    assert client.last_text().startswith("Готово")

    user = await _user(db)
    assert user.timezone == "Etc/GMT-4"
    assert user.morning_time == time(8, 15)
    assert user.session_weekday is None and user.session_time is None
    assert user.onboarding_completed


async def test_start_after_onboarding_shows_help(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/ping")
    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        user.onboarding_completed = True
    await client.send("/start")
    assert client.last_text() == ru.HELP


async def test_settings_changes(make_client: Any, db: Factory) -> None:
    spy = Spy()
    client = make_client(settings_changed=spy)
    await client.send("/settings")
    assert "Настройки" in client.last_text()

    await client.click_button(ru.BTN_REMINDERS_ON)
    assert not (await _user(db)).reminders_enabled

    await client.click_button(ru.BTN_SET_EVENING)
    await client.click_button("22:00")
    assert ru.SETTINGS_SAVED in client.last_text()
    assert (await _user(db)).evening_time == time(22)

    await client.click_button(ru.BTN_SET_QUIET)
    await client.click_button(ru.BTN_OTHER)
    await client.send("23:30-07:00")
    user = await _user(db)
    assert (user.quiet_hours_start, user.quiet_hours_end) == (time(23, 30), time(7))

    await client.click_button(ru.BTN_SET_SESSION)
    await client.click_button("Пт")
    await client.click_button("10:00")
    user = await _user(db)
    assert (user.session_weekday, user.session_time) == (4, time(10))
    assert len(spy.calls) == 4
