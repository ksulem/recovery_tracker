"""'Другое — опишу своими словами' in every list: a follow-up in own words."""

from datetime import UTC, datetime
from typing import Any

import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import Checkin, CheckinMark, CheckinSymptom, DayEpisode
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]
ODD_EVENING = datetime(2026, 11, 17, 17, 0, tzinfo=UTC)  # odd day: no pressure question
NEXT_MORNING = datetime(2026, 11, 18, 6, 0, tzinfo=UTC)


async def _week(client: Any) -> str:
    await client.send("/week")
    return str(client.documents()[-1].document.data.decode())


async def test_morning_other_answers(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(NEXT_MORNING, tick=False):
        await client.send("/morning")
        await client.click_button("Плохо")
        await client.click_button("Трудно по другой причине")
        assert client.last_text() == ru.Q_ONSET_NOTE
        await client.send("Шумели соседи")
        await client.click_button("Видео")
        await client.click_button("Другое")
        await client.click_button(ru.BTN_DONE)
        assert client.last_text() == ru.Q_PRE_SLEEP_NOTE
        await client.send("Листала новости")
        assert client.last_text() == ru.Q_BATTERY_MORNING
        await client.click_button(ru.BTN_FINISH)
        week = await _week(client)

    async with db() as s:
        c = await s.scalar(select(Checkin))
        marks = sorted((await s.scalars(select(CheckinMark.value))).all())
    assert c is not None
    assert (c.sleep_onset, c.sleep_onset_note, c.pre_sleep_note) == (
        "other",
        "Шумели соседи",
        "Листала новости",
    )
    assert marks == ["other", "video"]
    assert "заснуть: трудно по другой причине («Шумели соседи»)" in week
    assert "- Перед сном: видео, сериалы, другое: «Листала новости»" in week


async def test_no_follow_up_without_other(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(NEXT_MORNING, tick=False):
        await client.send("/morning")
        await client.click_button("Хорошо")
        await client.click_button("Легко")
        assert client.last_text().startswith(ru.Q_PRE_SLEEP)  # no "что мешало?"
        await client.click_button("Соцсети")
        await client.click_button(ru.BTN_DONE)
        assert client.last_text() == ru.Q_BATTERY_MORNING  # no "что ещё было?"


async def test_other_episode_and_resource(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(ODD_EVENING, tick=False):
        await client.send("/evening")
        for _ in range(3):
            await client.click_button(ru.BTN_SKIP)  # compared to morning, battery, obligations
        await client.click_button("Хобби")
        await client.click_button("Другое")
        await client.click_button(ru.BTN_DONE)
        assert client.last_text() == ru.Q_EPISODE_OTHER_NOTE  # right after picking
        await client.send("Ремонт у соседей")
        assert client.last_text() == "Хобби — как это было?"
        await client.click_button("Дало силы")
        assert client.last_text() == "Другое — как это было?"
        await client.click_button("И то, и другое")
        await client.click_button("Другое")
        await client.click_button(ru.BTN_DONE)  # what it gave: "другое"
        assert client.last_text() == "Другое: что ещё дало? Своими словами."
        await client.send("Повод выйти из дома")
        await client.click_button("Телу")
        await client.click_button(ru.BTN_DONE)  # what it took
        assert client.last_text() == ru.Q_PREOCCUPATION  # no note: "другое" not picked
        await client.click_button(ru.BTN_FINISH)
        week = await _week(client)

    async with db() as s:
        episodes = {e.kind: e for e in (await s.scalars(select(DayEpisode))).all()}
    other = episodes["other"]
    assert (other.note, other.effect, other.gave, other.gave_note, other.took) == (
        "Ремонт у соседей",
        "both",
        ["other"],
        "Повод выйти из дома",
        ["physical"],
    )
    assert (
        "  - другое: «Ремонт у соседей» — и то, и другое "
        "(дало: другое: «Повод выйти из дома»; забрало: телу)"
    ) in week


async def test_other_sign_of_overload_now(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(ODD_EVENING, tick=False):
        await client.send("/now")
        await client.click_button("30%")
        await client.click_button("3")
        await client.click_button("Раздражительность")
        await client.click_button("Другое")
        await client.click_button(ru.BTN_DONE)
        assert client.last_text() == ru.Q_SIGNS_NOTE
        await client.send("Тревога без понятной причины")
        assert client.last_text() == ru.Q_NOTE_NOW
        await client.click_button(ru.BTN_SKIP)
        week = await _week(client)

    async with db() as s:
        c = await s.scalar(select(Checkin))
        symptoms = sorted((await s.scalars(select(CheckinSymptom.symptom))).all())
    assert c is not None and c.signs_note == "Тревога без понятной причины"
    assert symptoms == ["irritability", "other"]
    assert "Признаки перегрузки: раздражительность, другое: «Тревога без понятной причины»" in week
