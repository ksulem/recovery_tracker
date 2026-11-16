import uuid
from datetime import UTC, date, datetime
from typing import Any

import time_machine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import Checkin, DayEpisode
from recovery.reports import patterns
from recovery.reports.digest import Day, Digest
from recovery.texts import ru


def _checkin(kind: str, day: date, **fields: Any) -> Checkin:
    hour = 7 if kind == "morning" else 18
    return Checkin(
        id=uuid.uuid4(),
        checkin_type=kind,
        status="completed",
        tz_name="UTC",
        created_at_utc=datetime(day.year, day.month, day.day, hour, tzinfo=UTC),
        local_date=day,
        local_ts=datetime(day.year, day.month, day.day, hour),
        question_status={},
        answers=fields.pop("answers", {}),
        **fields,
    )


def _episode(c: Checkin, kind: str, effect: str, choice: str | None = None, **kw: Any) -> Any:
    return DayEpisode(
        checkin_id=c.id,
        kind=kind,
        effect=effect,
        choice=choice,
        gave=kw.get("gave", []),
        took=kw.get("took", []),
    )


def week() -> Digest:
    """Four evening → morning pairs, 16–20 November."""
    days = {d: Day(date(2026, 11, d)) for d in range(16, 21)}

    def evening(
        d: int, episodes: list[tuple[Any, ...]], pressure: list[str] | None, **f: Any
    ) -> None:
        c = _checkin("evening", days[d].date, **f)
        c.answers = {**c.answers, "ep": [e[0] for e in episodes]}
        if pressure == []:
            c.answers["press"] = ["none"]
        days[d].checkins.append(c)
        for kind, effect, choice, extra in episodes:
            days[d].episodes[c.id].append(_episode(c, kind, effect, choice, **extra))
        if pressure:
            days[d].marks[(c.id, "pressure")] = pressure

    def morning(d: int, **f: Any) -> Checkin:
        c = _checkin("morning", days[d].date, **f)
        days[d].checkins.append(c)
        return c

    evening(
        16,
        [
            ("work_social", "both", "needed", {"gave": ["cognitive"], "took": ["emotional"]}),
            ("close_people", "gave", "wanted", {}),
        ],
        ["guilt_resting"],
        comparison_to_morning="better",
        battery_pct=60,
        obligations="many",
        preoccupation="strong",
    )
    m17 = morning(17, battery_pct=30, sleep_quality="medium", sleep_onset="thoughts")
    days[17].marks[(m17.id, "pre_sleep")] = ["shopping"]
    evening(
        17,
        [("close_people", "both", "wanted", {"gave": ["emotional"], "took": ["physical"]})],
        [],
        comparison_to_morning="better",
        battery_pct=50,
        obligations="none",
        preoccupation="none",
    )
    morning(18, battery_pct=60)
    evening(
        18,
        [("self_demands", "took", None, {})],
        None,
        comparison_to_morning="same",
        battery_pct=40,
        obligations="some",
        preoccupation="some",
    )
    morning(19, battery_pct=20)
    evening(
        19,
        [("close_people", "gave", "wanted", {})],
        ["guilt_resting"],
        comparison_to_morning="better",
        battery_pct=70,
        obligations="many",
        preoccupation="strong",
    )
    morning(20, battery_pct=50)
    return Digest(date(2026, 11, 16), date(2026, 11, 20), [days[d] for d in sorted(days)])


def test_evening_to_morning_pairs() -> None:
    text = patterns.evening_to_morning_section(week())
    assert text is not None
    assert text.startswith(ru.EVENING_MORNING_TITLE)
    assert (
        "- Пн 16 → Вт 17: вечер — по сравнению с утром: лучше, заряд 60%, обязательств: много, "
        "эпизоды: общение по работе или обязательное — оба; общение с близкими — дало, "
        "мысли занимает: да, сильно, давление: чувство вины за отдых; утро — после сна: "
        "средне, заснуть: долго крутились мысли, перед сном: покупки или каталоги, заряд 30%"
    ) in text
    assert "давление: нет" in text  # 17th: answered "ничего из этого"
    # 60→30, 50→60, 40→20, 70→50: lower by morning 3 times out of 4.
    assert ru.LOWER_BY_MORNING.format(count=3, total=4) in text
    assert ru.NOT_ENOUGH_DATA not in text.split("\n")[-1]


def test_episode_summary_counts_without_averages() -> None:
    text = patterns.episodes_section(week())
    assert text is not None
    lines = text.split("\n")
    assert lines[2] == (
        "- общение с близкими — 3: дало 2, оба 1 (при «оба» — дало: эмоционально 1; "
        "забрало: телу 1); по желанию: дало 2, оба 1"
    )
    assert (
        "- общение по работе или обязательное — 1: оба 1 (при «оба» — дало: голове 1; "
        "забрало: эмоционально 1); по необходимости: оба 1 (данных недостаточно)"
    ) in lines
    assert "- требования к себе — 1: забрало 1 (данных недостаточно)" in lines


def test_hypotheses_line_facts_up() -> None:
    text = patterns.hypotheses_section(week())
    assert text is not None
    assert ru.HYPOTHESES_DISCLAIMER in text
    # 1. Busy evenings: 16, 18, 19 → mornings 30, 20, 50; calm: 17 → 60 (one case).
    assert "- что-то занимало мысли: 3 веч., утром заряд: 30%, 20%, 50%\n" in text
    assert "- ничего не занимало: 1 веч., утром заряд: 60% (данных недостаточно)" in text
    # 2. Obligations.
    assert "- много: 2 веч., утром заряд: 30%, 50% (данных недостаточно)" in text
    # 3. Chosen vs needed.
    assert "- по желанию: 3 — дало 2, оба 1\n" in text
    assert "- по необходимости: 1 — оба 1 (данных недостаточно)" in text
    # 4. Pressure: asked on 16, 17, 19; with pressure on 16 and 19.
    assert "- вечеров с вопросом: 3, из них с давлением: 2 (чувство вины за отдых 2)" in text
    assert "- «требования к себе» как эпизод: 1 — забрало 1 (данных недостаточно)" in text
    # 5. "Better than in the morning" on 16, 17, 19; lower next morning on 16 and 19.
    assert "- вечеров «лучше, чем утром»: 3; утром заряд ниже вечернего: 2 из 3" in text


def test_nothing_to_show_without_data() -> None:
    empty = Digest(date(2026, 11, 16), date(2026, 11, 17), [Day(date(2026, 11, 16))])
    assert patterns.evening_to_morning_section(empty) is None
    assert patterns.episodes_section(empty) is None
    assert patterns.hypotheses_section(empty) is None


async def test_sections_appear_in_the_session_brief(
    make_client: Any, db: async_sessionmaker[AsyncSession]
) -> None:
    client = make_client()
    with time_machine.travel(datetime(2026, 11, 17, 17, tzinfo=UTC), tick=False):  # odd day
        await client.send("/evening")
        await client.click_button("Лучше")
        await client.click_button("70%")
        await client.click_button("Не было")
        await client.click_button("Хобби")
        await client.click_button(ru.BTN_DONE)
        await client.click_button("Дало силы")
        await client.click_button("Нет")  # preoccupation
        await client.click_button(ru.BTN_FINISH)
    with time_machine.travel(datetime(2026, 11, 18, 6, tzinfo=UTC), tick=False):
        await client.send("/morning")
        await client.click_button("Хорошо")
        await client.click_button("Легко")
        await client.click_button("Ничего из этого")
        await client.click_button(ru.BTN_DONE)
        await client.click_button("40%")
        await client.click_button(ru.BTN_FINISH)
        await client.send("/session")
    brief = client.documents()[-1].document.data.decode()
    assert (
        "- Вт 17 → Ср 18: вечер — по сравнению с утром: лучше, заряд 70%, обязательств: "
        "не было, эпизоды: хобби — дало, мысли занимает: нет; утро — после сна: хорошо, "
        "заснуть: легко, заряд 40%"
    ) in brief
    assert "## Эпизоды за период\n\n- хобби — 1: дало 1 (данных недостаточно)" in brief
    assert ru.HYPOTHESES_SECTION_TITLE in brief
    assert brief.index("## Гипотезы") < brief.index("## По дням")
