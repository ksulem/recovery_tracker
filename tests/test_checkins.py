from datetime import UTC, date, datetime
from typing import Any

import time_machine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import Checkin, CheckinSymptom, LoadEvent, RecoveryEvent, User
from recovery.domain import CheckinStatus, CheckinType
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]


async def _checkins(db: Factory, kind: str | None = None) -> list[Checkin]:
    async with db() as s:
        stmt = select(Checkin).order_by(Checkin.created_at_utc)
        if kind:
            stmt = stmt.where(Checkin.checkin_type == kind)
        return list((await s.scalars(stmt)).all())


async def _count(db: Factory, model: Any) -> int:
    async with db() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def test_morning_checkin_saves_every_answer(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    assert ru.INTRO_MORNING in client.texts()
    assert client.last_text() == ru.Q_SLEEP

    await client.click_button("Хорошо")
    await client.click_button("6")
    await client.click_button("Немного")
    await client.click_button("Терпимо")
    assert client.last_text() == ru.Q_EXPECTED_LOAD
    await client.send("Длинный созвон <важный>")
    assert client.last_text() == ru.OUTRO_MORNING

    [c] = await _checkins(db)
    assert c.status == CheckinStatus.COMPLETED
    assert c.completed_at_utc is not None
    assert (c.sleep_quality, c.physical_score, c.pleasant_desire, c.day_outlook) == (
        "good",
        6,
        "a_little",
        "tolerable",
    )
    assert c.expected_heavy_load == "Длинный созвон <важный>"
    assert set(c.question_status.values()) == {"answered"}
    # The answered question shows the chosen answer and loses its keyboard.
    assert any("→ Хорошо" in t for t in client.texts())


async def test_skipping_everything_leaves_nulls_not_zeros(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    for _ in range(5):
        await client.click_button(ru.BTN_SKIP)
    assert client.last_text() == ru.OUTRO_MORNING

    [c] = await _checkins(db)
    assert c.status == CheckinStatus.COMPLETED
    assert c.sleep_quality is None and c.physical_score is None and c.day_outlook is None
    assert c.question_status == {
        "sleep": "skipped",
        "phys": "skipped",
        "desire": "skipped",
        "outlook": "skipped",
        "heavy": "skipped",
    }


async def test_finish_early_keeps_what_was_answered(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/evening")
    await client.click_button("Лучше")
    await client.click_button(ru.BTN_FINISH)
    assert client.last_text() == ru.OUTRO_EVENING

    [c] = await _checkins(db)
    assert c.status == CheckinStatus.COMPLETED
    assert c.comparison_to_morning == "better"
    assert c.question_status == {"cmp": "answered"}
    assert await _count(db, LoadEvent) == 0


async def test_evening_short_path_with_multi_select(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/evening")
    await client.click_button("Хуже")
    await client.click_button("Работа")
    await client.click_button("Общение")
    await client.click_button("Работа")  # untoggle
    await client.click_button("Переживания")
    await client.click_button(ru.BTN_DONE)
    await client.click_button("Хобби")
    await client.click_button(ru.BTN_DONE)
    await client.click_button("Не знаю")
    assert client.last_text() == ru.Q_MORE_DETAILS
    await client.click_button(ru.BTN_NO)
    assert client.last_text() == ru.Q_TOMORROW  # detail block left out
    await client.click_button(ru.BTN_SKIP)
    assert client.last_text() == ru.OUTRO_EVENING

    async with db() as s:
        categories = sorted((await s.scalars(select(LoadEvent.category))).all())
        activities = (await s.scalars(select(RecoveryEvent.activity))).all()
    assert categories == ["emotional", "social"]
    assert activities == ["hobby"]
    [c] = await _checkins(db)
    assert c.pleasant_energy == "unknown"
    assert c.physical_score is None and c.interest_score is None
    assert c.question_status["more"] == "no"


async def test_evening_detail_block(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/evening")
    await client.click_button(ru.BTN_SKIP)  # cmp
    await client.click_button(ru.BTN_DONE)  # drains: nothing selected
    await client.click_button(ru.BTN_SKIP)  # helped
    await client.click_button(ru.BTN_SKIP)  # energy
    await client.click_button(ru.BTN_YES)
    for score in ("8", "2", "3", "4", "5"):
        await client.click_button(score)
    assert client.last_text().startswith(ru.Q_INTEREST)
    await client.click_button("3 —")
    await client.click_button("Раздражительность")
    await client.click_button("слёзы")
    await client.click_button(ru.BTN_DONE)
    await client.send("Не планировать встречи")
    assert client.last_text() == ru.OUTRO_EVENING

    [c] = await _checkins(db)
    scores = (
        c.physical_score,
        c.emotional_score,
        c.social_score,
        c.cognitive_score,
        c.overall_score,
    )
    assert scores == (8, 2, 3, 4, 5)  # stored separately, never averaged
    assert c.interest_score == 3
    assert c.question_status["drain"] == "skipped"
    assert c.tomorrow_note == "Не планировать встречи"
    async with db() as s:
        symptoms = sorted((await s.scalars(select(CheckinSymptom.symptom))).all())
    assert symptoms == ["emotional_instability", "irritability"]


async def test_check_in_survives_a_restart(make_client: Any, db: Factory) -> None:
    before = make_client()
    await before.send("/morning")
    await before.click_button("Плохо")

    # New dispatcher, new storage instance: only Postgres carries the state.
    after = make_client()
    after.session.requests = list(before.session.requests)
    await after.click_button("3")  # the button sent before the restart still works
    assert after.last_text() == ru.Q_PLEASANT_DESIRE

    [c] = await _checkins(db)
    assert (c.sleep_quality, c.physical_score) == ("poor", 3)
    assert c.status == CheckinStatus.IN_PROGRESS


async def test_resume_and_restart(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    await client.click_button("Средне")
    await client.send("/morning")
    assert client.last_text() == ru.RESUME_PROMPT.format(name="утренний")
    await client.click_button(ru.BTN_CONTINUE)
    assert client.last_text().startswith(ru.Q_PHYSICAL_NOW)  # picks up where it stopped

    await client.send("/morning")
    await client.click_button(ru.BTN_RESTART)
    assert client.last_text() == ru.Q_SLEEP
    old, new = await _checkins(db)
    assert old.status == CheckinStatus.ABANDONED and old.sleep_quality == "medium"
    assert new.status == CheckinStatus.IN_PROGRESS and new.question_status == {}


async def test_completed_checkin_is_not_repeated(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    await client.click_button(ru.BTN_FINISH)
    await client.send("/morning")
    assert client.last_text() == ru.ALREADY_DONE.format(name="утренний")
    assert len(await _checkins(db)) == 1


async def test_old_buttons_are_rejected(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    first_question = client.last_with_keyboard()
    await client.click_button("Хорошо")
    # Click "Плохо" on the already-answered first question.
    client.session.requests.append(first_question)
    await client.click_button("Плохо")
    assert client.last_text() == ru.STALE_QUESTION
    [c] = await _checkins(db)
    assert c.sleep_quality == "good"


async def test_text_during_a_button_question_becomes_a_note(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    await client.send("Голова тяжёлая")
    assert client.last_text() == ru.NOTE_ADDED
    [c] = await _checkins(db)
    assert c.notes == "Голова тяжёлая"
    assert c.question_status == {}


async def test_ad_hoc_checkins_are_unlimited(make_client: Any, db: Factory) -> None:
    client = make_client()
    for _ in range(2):
        await client.send("/now")
        await client.click_button("2")
        await client.click_button("Хочется, чтобы никто не трогал")
        await client.click_button(ru.BTN_DONE)
        await client.click_button(ru.BTN_SKIP)
        assert client.last_text() == ru.OUTRO_AD_HOC
    rows = await _checkins(db, CheckinType.AD_HOC)
    assert [r.overall_score for r in rows] == [2, 2]
    assert await _count(db, CheckinSymptom) == 2


async def test_scheduled_offer_and_not_now(make_client: Any, db: Factory) -> None:
    from recovery.telegram.routers.checkin import send_offer
    from tests.tg import OWNER_ID

    client = make_client()
    await send_offer(client.bot, OWNER_ID, CheckinType.EVENING)
    await client.click_button(ru.BTN_NOT_NOW)
    assert client.last_text() == ru.NOT_NOW
    assert await _checkins(db) == []

    await send_offer(client.bot, OWNER_ID, CheckinType.EVENING)
    await client.click_button(ru.BTN_START)
    assert client.last_text() == ru.Q_COMPARISON


async def test_local_date_uses_the_owner_timezone(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/ping")  # creates the user row
    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        user.timezone = "Asia/Tbilisi"
    # 21:30 UTC on Nov 7 is already Nov 8 in Tbilisi.
    with time_machine.travel(datetime(2026, 11, 7, 21, 30, tzinfo=UTC), tick=False):
        await client.send("/evening")
    [c] = await _checkins(db)
    assert c.tz_name == "Asia/Tbilisi"
    assert c.local_date == date(2026, 11, 8)
    assert c.created_at_utc == datetime(2026, 11, 7, 21, 30, tzinfo=UTC)
