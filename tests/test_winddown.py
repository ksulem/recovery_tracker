from datetime import UTC, datetime, time
from typing import Any

import pytest
import time_machine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery import winddown
from recovery.db.models import Checkin, DiscussionItem, Message, ParkedThought, User
from recovery.domain import PromptKind, ThoughtStatus
from recovery.scheduler.service import CheckinScheduler, planned_jobs
from recovery.telegram.routers.thoughts import send_pre_walk
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]
TBILISI = "Asia/Tbilisi"  # UTC+4, no DST


def local(day: int, hour: int, minute: int = 0) -> datetime:
    """A moment in November 2026 given in Tbilisi local time (UTC+4)."""
    total = hour * 60 + minute - 4 * 60
    if total < 0:
        day, total = day - 1, total + 24 * 60
    return datetime(2026, 11, day, total // 60, total % 60, tzinfo=UTC)


def owner(**fields: Any) -> User:
    defaults: dict[str, Any] = {
        "timezone": TBILISI,
        "morning_time": time(9, 30),
        "evening_time": time(20, 45),
        "bedtime": time(23),
        "walk_time": time(18, 45),
        "reminders_enabled": True,
        "battery_enabled": True,
    }
    return User(**(defaults | fields))


async def _setup(client: Any, db: Factory, **fields: Any) -> User:
    with time_machine.travel(local(16, 12), tick=False):
        await client.send("/ping")  # creates the user row
    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        values = {
            "timezone": TBILISI,
            "evening_time": time(20, 45),
            "bedtime": time(23),
            "walk_time": time(18, 45),
            "onboarding_completed": True,
        } | fields
        for key, value in values.items():
            setattr(user, key, value)
    client.clear()
    return user


async def _thoughts(db: Factory) -> list[ParkedThought]:
    async with db() as s:
        stmt = select(ParkedThought).order_by(ParkedThought.seq)
        return list((await s.scalars(stmt)).all())


async def _count(db: Factory, model: Any) -> int:
    async with db() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


def _service(client: Any, db: Factory) -> CheckinScheduler:
    return CheckinScheduler(
        AsyncIOScheduler(timezone="UTC"),
        client.bot,
        db,
        evening_reminder_delay_minutes=45,
        extra_senders={PromptKind.PRE_WALK: send_pre_walk},
    )


def _bring_to_front(client: Any, text_part: str) -> None:
    """Make an earlier message with buttons the one ``click_button`` uses."""
    msg = next(
        r
        for r in reversed(client.session.requests)
        if text_part in (getattr(r, "text", None) or "") and getattr(r, "reply_markup", None)
    )
    client.session.requests.append(msg)


# --- timing ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("now", "late", "night"),
    [
        (time(21, 59), False, False),
        (time(22, 0), True, False),
        (time(23, 30), True, False),
        (time(0, 30), True, True),
        (time(5, 59), True, True),
        (time(6, 0), False, False),
        (time(14, 0), False, False),
    ],
)
def test_late_window(now: time, late: bool, night: bool) -> None:
    user = owner()
    assert winddown.boundary(user) == time(22)
    assert winddown.is_late(user, now) is late
    assert winddown.is_night(user, now) is night


def test_no_bedtime_means_no_late_mode() -> None:
    user = owner(bedtime=None)
    assert not winddown.is_late(user, time(23, 30))
    assert winddown.boundary(user) is None


def test_late_window_after_midnight_bedtime() -> None:
    user = owner(bedtime=time(0, 30))
    assert winddown.boundary(user) == time(23, 30)
    assert winddown.is_late(user, time(7, 0))
    assert not winddown.is_late(user, time(8, 0))


def test_evening_suggestion_and_too_late() -> None:
    assert winddown.suggested_evening_time(owner()) == time(20, 45)
    assert not winddown.evening_too_late(owner(evening_time=time(21, 0)))
    assert winddown.evening_too_late(owner(evening_time=time(21, 50)))
    assert winddown.evening_too_late(owner(evening_time=time(22, 30)))
    assert not winddown.evening_too_late(owner(evening_time=time(22, 30), bedtime=None))


def test_on_time() -> None:
    assert winddown.on_time(time(21, 10), time(23))
    assert not winddown.on_time(time(22, 5), time(23))
    assert not winddown.on_time(time(0, 30), time(23))


def test_jobs_for_the_agreed_schedule() -> None:
    jobs = planned_jobs(owner(), 120, 45)
    start = datetime(2026, 11, 16, tzinfo=UTC)

    def next_local(kind: PromptKind) -> time:
        # Triggers carry the owner's timezone, so fire times are local.
        return jobs[kind].get_next_fire_time(None, start).time()

    assert next_local(PromptKind.PRE_WALK) == time(18, 45)
    assert next_local(PromptKind.EVENING) == time(20, 45)
    assert next_local(PromptKind.EVENING_REMINDER) == time(21, 30)
    assert next_local(PromptKind.MORNING_REMINDER) == time(11, 30)
    assert PromptKind.PRE_WALK not in planned_jobs(owner(walk_time=None), 120, 45)


# --- parking thoughts --------------------------------------------------------------
async def test_late_text_is_parked_with_one_line(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 22, 30), tick=False):
        await client.send("Не могу перестать думать про отчёт")
    assert client.texts() == [ru.LATE_PARKED]
    assert client.last_with_keyboard() is None  # nothing to tap late at night
    [t] = await _thoughts(db)
    assert t.text == "Не могу перестать думать про отчёт"
    assert await _count(db, Message) == 0  # parked, not a diary note


async def test_daytime_note_can_be_parked(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 15), tick=False):
        await client.send("Разговор с начальником")
        assert client.last_text() == ru.NOTE_SAVED
        await client.click_button(ru.BTN_PARK)
        assert client.last_text() == ru.PARKED
        await client.click_button(ru.BTN_PARK)  # twice → no duplicate
    [t] = await _thoughts(db)
    assert t.message_id is not None and t.text == "Разговор с начальником"
    assert await _count(db, Message) == 1


async def test_later_command(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 15), tick=False):
        await client.send("/later Позвонить врачу\nОтветить Маше")
        assert client.last_text() == ru.PARKED_MANY.format(count=2)
        await client.send("/later")
        listing = client.last_text()
        assert "1. Позвонить врачу" in listing and "2. Ответить Маше" in listing
        await client.send("Купить подарок")
        assert client.last_text() == ru.PARKED
    with time_machine.travel(local(16, 22, 30), tick=False):
        await client.send("/later")
        assert client.last_text() == ru.LATER_PROMPT_LATE  # no list late at night
        await client.send("Ещё одна мысль")
        assert client.last_text() == ru.LATE_PARKED
    assert len(await _thoughts(db)) == 4


async def test_review_one_at_a_time(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 15), tick=False):
        await client.send("/later Первая\nВторая\nТретья")
        await client.send("/later")
        await client.click_button(ru.BTN_REVIEW)
        assert client.last_text().startswith("«Первая»")
        await client.click_button("Обдумано")
        assert client.last_text().startswith("«Вторая»")
        await client.click_button("На сессию")
        assert client.last_text().startswith("«Третья»")
        await client.click_button("Пусть полежит")
        assert client.last_text() == ru.REVIEW_DONE
    statuses = {t.text: (t.status, t.postponed_count) for t in await _thoughts(db)}
    assert statuses == {
        "Первая": (ThoughtStatus.DONE, 0),
        "Вторая": (ThoughtStatus.TO_SESSION, 0),
        "Третья": (ThoughtStatus.OPEN, 1),
    }
    async with db() as s:
        assert (await s.scalars(select(DiscussionItem.text))).all() == ["Вторая"]


# --- the evening walk --------------------------------------------------------------
async def test_pre_walk_offer(make_client: Any, db: Factory) -> None:
    client = make_client()
    user = await _setup(client, db)
    service = _service(client, db)

    with time_machine.travel(local(16, 18, 45), tick=False):
        await service.fire(user.id, PromptKind.PRE_WALK)
    assert client.texts() == []  # nothing parked → nothing sent

    with time_machine.travel(local(17, 12), tick=False):
        await client.send("/later Старая мысль\nНовая мысль")
    client.clear()
    with time_machine.travel(local(17, 18, 45), tick=False):
        await service.fire(user.id, PromptKind.PRE_WALK)
        assert client.last_text() == ru.PRE_WALK.format(count=2)
        await client.click_button(ru.BTN_JUST_WALK)
        assert client.last_text() == ru.JUST_WALK
    assert all(t.taken_on is None for t in await _thoughts(db))

    with time_machine.travel(local(18, 18, 45), tick=False):
        await service.fire(user.id, PromptKind.PRE_WALK)
        await client.click_button(ru.BTN_TAKE_ONE)
        assert "«Старая мысль»" in client.last_text()
        await client.click_button(ru.BTN_ANOTHER)
        assert "«Новая мысль»" in client.last_text()
    taken = {t.text: t.taken_on for t in await _thoughts(db)}
    assert taken["Новая мысль"] is not None and taken["Старая мысль"] is None


async def test_no_pre_walk_offer_in_late_mode(make_client: Any, db: Factory) -> None:
    client = make_client()
    user = await _setup(client, db, walk_time=time(22, 30))
    with time_machine.travel(local(16, 12), tick=False):
        await client.send("/later Мысль")
    client.clear()
    with time_machine.travel(local(16, 22, 30), tick=False):
        await _service(client, db).fire(user.id, PromptKind.PRE_WALK)
    assert client.texts() == []


async def _evening_to_closing(client: Any) -> None:
    await client.send("/evening")
    await client.click_button(ru.BTN_SKIP)  # compared to morning
    await client.click_button(ru.BTN_SKIP)  # battery
    await client.click_button(ru.BTN_DONE)  # drains
    await client.click_button(ru.BTN_DONE)  # helped
    await client.click_button(ru.BTN_SKIP)  # energy
    await client.click_button(ru.BTN_NO)  # no details
    await client.click_button(ru.BTN_SKIP)  # tomorrow


async def test_evening_closes_the_day(make_client: Any, db: Factory) -> None:
    client = make_client()
    user = await _setup(client, db)
    with time_machine.travel(local(16, 12), tick=False):
        await client.send("/later Отчёт для клиента")
    with time_machine.travel(local(16, 18, 45), tick=False):
        await _service(client, db).fire(user.id, PromptKind.PRE_WALK)
        await client.click_button(ru.BTN_TAKE_ONE)
    with time_machine.travel(local(16, 20, 45), tick=False):
        await _evening_to_closing(client)
        assert client.last_text() == f"{ru.Q_WALK_THOUGHT}\n«Отчёт для клиента»"
        await client.click_button("Пусть полежит")
        assert client.last_text() == ru.Q_PARK
        await client.send("Купить корм\n\nНаписать Ане")
        assert client.last_text() == ru.OUTRO_EVENING
    thoughts = {t.text: (t.status, t.postponed_count) for t in await _thoughts(db)}
    assert thoughts == {
        "Отчёт для клиента": (ThoughtStatus.OPEN, 1),
        "Купить корм": (ThoughtStatus.OPEN, 0),
        "Написать Ане": (ThoughtStatus.OPEN, 0),
    }
    async with db() as s:
        c = await s.scalar(select(Checkin))
    assert c is not None and c.bedtime == time(23)
    assert c.question_status["walk"] == "answered" and c.question_status["park"] == "answered"


async def test_walk_question_is_skipped_when_nothing_was_taken(
    make_client: Any, db: Factory
) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 20, 45), tick=False):
        await _evening_to_closing(client)
        assert client.last_text() == ru.Q_PARK
        await client.click_button(ru.BTN_NOTHING)
        assert client.last_text() == ru.OUTRO_EVENING
    assert await _thoughts(db) == []


async def test_third_postponement_offers_the_session(make_client: Any, db: Factory) -> None:
    client = make_client()
    user = await _setup(client, db)
    with time_machine.travel(local(16, 12), tick=False):
        await client.send("/later Отношения с мамой")
    async with db() as s, s.begin():
        thought = await s.scalar(select(ParkedThought))
        assert thought is not None
        thought.postponed_count = 2
    with time_machine.travel(local(16, 18, 45), tick=False):
        await _service(client, db).fire(user.id, PromptKind.PRE_WALK)
        await client.click_button(ru.BTN_TAKE_ONE)
    with time_machine.travel(local(16, 20, 45), tick=False):
        await _evening_to_closing(client)
        await client.click_button("Пусть полежит")
        suggestion = ru.THOUGHT_SUGGEST_SESSION.format(text="Отношения с мамой")
        assert suggestion in client.texts()
        _bring_to_front(client, "возвращается уже третий раз")
        await client.click_button(ru.BTN_THOUGHT_SESSION)
        assert client.last_text() == ru.THOUGHT_MOVED_TO_SESSION
    [t] = await _thoughts(db)
    assert (t.status, t.session_suggested) == (ThoughtStatus.TO_SESSION, True)
    async with db() as s:
        assert (await s.scalars(select(DiscussionItem.text))).all() == ["Отношения с мамой"]


# --- late evening ------------------------------------------------------------------
async def test_late_evening_offers_a_short_version(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db, battery_reserve_pct=None)
    with time_machine.travel(local(16, 22, 30), tick=False):
        await client.send("/evening")
        assert client.last_text() == ru.LATE_EVENING_OFFER
        await client.click_button(ru.BTN_SHORT)
        assert client.last_text() == ru.Q_BATTERY_NOW
        await client.click_button("30%")
        assert client.last_text() == ru.Q_PARK
        await client.click_button(ru.BTN_NOTHING)
        assert client.last_text() == ru.OUTRO_EVENING_SHORT
    async with db() as s:
        c = await s.scalar(select(Checkin))
    assert c is not None
    assert (c.flow_variant, c.battery_pct, c.bedtime) == ("short", 30, time(23))


async def test_late_evening_full_version_on_request(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 22, 30), tick=False):
        await client.send("/evening")
        await client.click_button(ru.BTN_FULL)
        assert client.last_text() == ru.Q_COMPARISON


async def test_after_midnight_only_parking(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(17, 0, 40), tick=False):
        await client.send("/evening")
        assert client.last_text() == ru.NIGHT_PARK_ONLY
        await client.send("Завтра дедлайн")
        assert client.last_text() == ru.LATE_PARKED
    assert await _count(db, Checkin) == 0
    assert [t.text for t in await _thoughts(db)] == ["Завтра дедлайн"]


async def test_no_reminder_in_late_mode(make_client: Any, db: Factory) -> None:
    client = make_client()
    user = await _setup(client, db, evening_time=time(21, 30))
    service = _service(client, db)
    with time_machine.travel(local(16, 21, 30), tick=False):
        await service.fire(user.id, PromptKind.EVENING)
    with time_machine.travel(local(16, 22, 15), tick=False):
        await service.fire(user.id, PromptKind.EVENING_REMINDER)
    assert client.texts() == [ru.OFFER_EVENING]


# --- settings ----------------------------------------------------------------------
async def test_bedtime_and_walk_settings(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db, bedtime=None, walk_time=None, evening_time=time(21, 0))
    with time_machine.travel(local(16, 15), tick=False):
        await client.send("/settings")
        assert ru.SUMMARY_NO_BEDTIME in client.last_text()
        await client.click_button(ru.BTN_SET_BEDTIME)
        assert client.last_text() == ru.ASK_BEDTIME
        await client.click_button("23:00")
        assert "Сон: около 23:00; с 22:00" in client.last_text()

        await client.click_button(ru.BTN_SET_EVENING)
        await client.click_button(ru.BTN_OTHER)
        await client.send("21:50")
        assert client.last_text() == ru.EVENING_TOO_LATE.format(evening="21:50", suggested="20:45")
        await client.click_button("Перенести на 20:45")
        assert ru.SETTINGS_SAVED in client.last_text()

        await client.click_button(ru.BTN_SET_WALK)
        await client.click_button("18:45")
        assert "Вечерняя прогулка: 18:45" in client.last_text()
        await client.click_button(ru.BTN_SET_WALK)
        await client.click_button(ru.BTN_NOT_SET)
    async with db() as s:
        user = await s.scalar(select(User))
    assert user is not None
    assert (user.bedtime, user.evening_time, user.walk_time) == (time(23), time(20, 45), None)


# --- reports -----------------------------------------------------------------------
async def test_reports_count_on_time_reviews_and_thoughts(make_client: Any, db: Factory) -> None:
    client = make_client()
    await _setup(client, db)
    with time_machine.travel(local(16, 20, 45), tick=False):
        await _evening_to_closing(client)
        await client.send("Мысль на завтра")  # parked in the closing step
    with time_machine.travel(local(17, 22, 30), tick=False):
        await client.send("/evening")
        await client.click_button(ru.BTN_SHORT)
        await client.click_button(ru.BTN_SKIP)
        await client.click_button(ru.BTN_NOTHING)
    with time_machine.travel(local(18, 15), tick=False):
        await client.send("/later")
        await client.click_button(ru.BTN_REVIEW)
        await client.click_button("Обдумано")
        await client.send("/session")
        summary = client.texts()[-1]
        brief = client.documents()[-1].document.data.decode()
        await client.send("/export_all")
        export = client.documents()[-1].document.data.decode()

    on_time = ru.DAY_REVIEW_ON_TIME.format(count=1, total=2)
    assert on_time in summary and on_time in brief
    assert "## Вечер и сон" in brief
    assert ru.THOUGHTS_SUMMARY.format(parked=1, done=1, session=0, dropped=0, open=0) in brief
    assert '"parked_thoughts"' in export and "Мысль на завтра" in export
