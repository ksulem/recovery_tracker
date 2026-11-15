from datetime import UTC, datetime
from typing import Any

import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import User
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]
EVEN_EVENING = datetime(2026, 11, 16, 17, 0, tzinfo=UTC)  # 16 Nov: even ordinal → pressure asked
NEXT_MORNING = datetime(2026, 11, 17, 6, 0, tzinfo=UTC)


async def _evening(client: Any) -> None:
    await client.send("/evening")
    await client.click_button("Лучше")
    await client.click_button("60%")
    await client.click_button("Много")  # obligations
    await client.click_button("Общение по работе")
    await client.click_button(ru.BTN_DONE)
    await client.click_button("И то, и другое")
    await client.click_button("Голове")
    await client.click_button(ru.BTN_DONE)  # gave
    await client.click_button("Эмоционально")
    await client.click_button(ru.BTN_DONE)  # took
    await client.click_button("По необходимости")
    await client.click_button("Немного")  # preoccupation
    await client.send("Сроки по проекту")
    await client.click_button(ru.BTN_SKIP)  # energy
    await client.click_button("Ничего из этого")
    await client.click_button(ru.BTN_DONE)  # pressure: answered "none"
    await client.click_button(ru.BTN_FINISH)


async def test_new_answers_in_the_week_file(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(EVEN_EVENING, tick=False):
        await _evening(client)
    with time_machine.travel(NEXT_MORNING, tick=False):
        await client.send("/morning")
        await client.click_button("Средне")
        await client.click_button("Долго крутились мысли")
        await client.click_button("Соцсети")
        await client.click_button(ru.BTN_DONE)
        await client.click_button("30%")
        await client.click_button(ru.BTN_FINISH)
        await client.send("/week")
    text = client.documents()[-1].document.data.decode()

    assert "- По сравнению с утром: лучше" in text
    assert "- Обязательств: много" in text
    assert (
        "- Эпизоды дня:\n  - общение по работе или обязательное — и то, и другое "
        "(дало: голове; забрало: эмоционально; по необходимости)"
    ) in text
    assert "- Занимает мысли: немного — «Сроки по проекту»" in text
    assert "- Давление на себя: ничего из этого" in text
    assert "после сна: средне · заснуть: долго крутились мысли · заряд: 30%" in text
    assert "- Перед сном: соцсети" in text

    await client.send("/export_all")
    export = client.documents()[-1].document.data.decode()
    assert '"day_episodes"' in export and '"work_social"' in export
    assert '"checkin_marks"' in export and '"social_media"' in export


async def test_switched_off_sets_are_not_asked(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/ping")
    async with db() as s, s.begin():
        user = await s.scalar(select(User))
        assert user is not None
        user.disabled_features = ["hypotheses", "sleep"]
    with time_machine.travel(EVEN_EVENING, tick=False):
        await client.send("/evening")
        await client.click_button("Лучше")
        await client.click_button("60%")
        assert client.last_text() == ru.Q_EPISODES + ru.MULTI_HINT  # no obligations
        await client.click_button("Общение с близкими")
        await client.click_button(ru.BTN_DONE)
        await client.click_button("Дало силы")
        assert client.last_text() == ru.Q_PLEASANT_ENERGY  # no "why", no preoccupation
        await client.click_button(ru.BTN_SKIP)
        assert client.last_text() == ru.Q_MORE_DETAILS  # no pressure, even on an even day
    with time_machine.travel(NEXT_MORNING, tick=False):
        await client.send("/morning")
        await client.click_button("Средне")
        assert client.last_text() == ru.Q_BATTERY_MORNING  # no sleep questions
