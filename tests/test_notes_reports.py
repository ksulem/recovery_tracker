import json
from datetime import UTC, datetime
from typing import Any

import time_machine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.db.models import (
    Checkin,
    DialogState,
    DiscussionItem,
    Message,
    SafetyEvent,
    TherapySession,
    User,
)
from recovery.domain import CheckinStatus, DiscussionStatus, PromptKind
from recovery.scheduler.service import CheckinScheduler
from recovery.telegram.routers.reports import send_session_brief
from recovery.texts import ru

Factory = async_sessionmaker[AsyncSession]


def day(d: int, hour: int = 12) -> datetime:
    return datetime(2026, 11, d, hour, 0, tzinfo=UTC)


async def _count(db: Factory, model: Any, *where: Any) -> int:
    async with db() as s:
        return int(await s.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _document_text(client: Any, index: int = -1) -> tuple[str, str]:
    doc = client.documents()[index].document
    return doc.filename, doc.data.decode()


# --- free text and the session list -------------------------------------------------
async def test_free_text_is_saved_and_can_go_to_the_session_list(
    make_client: Any, db: Factory
) -> None:
    client = make_client()
    await client.send("Сегодня был тяжёлый разговор с мамой")
    assert client.last_text() == ru.NOTE_SAVED
    async with db() as s:
        note = await s.scalar(select(Message))
    assert note is not None and note.text == "Сегодня был тяжёлый разговор с мамой"

    await client.click_button(ru.BTN_TO_SESSION)
    assert client.last_text() == ru.DISCUSS_ADDED.format(count=1)
    await client.click_button(ru.BTN_TO_SESSION)  # twice → no duplicate
    assert client.last_text() == ru.DISCUSS_ALREADY
    assert await _count(db, DiscussionItem) == 1


async def test_discuss_command(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/discuss Почему откладываю отдых")
    assert client.last_text() == ru.DISCUSS_ADDED.format(count=1)

    await client.send("/discuss")
    texts = client.texts()
    assert "Почему откладываю отдых" in texts[-2]
    assert texts[-1] == ru.DISCUSS_PROMPT
    await client.send("Сон по выходным")
    assert client.last_text() == ru.DISCUSS_ADDED.format(count=2)

    await client.send("/discuss")
    await client.send("/cancel")
    assert client.last_text() == ru.CANCELLED
    await client.send("просто заметка")
    assert client.last_text() == ru.NOTE_SAVED  # not added to the list
    assert await _count(db, DiscussionItem) == 2


async def test_unknown_command(make_client: Any) -> None:
    client = make_client()
    await client.send("/nonsense")
    assert client.last_text().startswith("Не знаю такой команды")


# --- safety ---------------------------------------------------------------------------
async def test_crisis_message_stops_the_flow(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/morning")
    await client.click_button("Плохо")
    await client.send("Я больше не хочу жить")

    assert client.last_text() == ru.CRISIS.format(number="112")
    assert ru.NOTE_ADDED not in client.texts()
    assert await _count(db, SafetyEvent) == 1
    # The words are kept in the diary like any note, and the check-in can resume.
    assert await _count(db, Message, Message.text == "Я больше не хочу жить") == 1
    async with db() as s:
        c = await s.scalar(select(Checkin))
    assert c is not None and c.status == CheckinStatus.IN_PROGRESS and c.notes is None
    await client.click_button("5")  # the open question still works
    assert client.last_text() == ru.Q_PLEASANT_DESIRE


async def test_crisis_number_is_configurable(make_client: Any) -> None:
    client = make_client(emergency_number="116 123")
    await client.send("хочется умереть")
    assert "<b>116 123</b>" in client.last_text()


# --- /week ----------------------------------------------------------------------------
async def test_week_export_reflects_exactly_the_records(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(day(3, 7), tick=False):
        await client.send("/morning")
        await client.click_button("Хорошо")
        await client.click_button("7")
        for _ in range(3):
            await client.click_button(ru.BTN_SKIP)
    with time_machine.travel(day(5, 19), tick=False):
        await client.send("/evening")
        await client.click_button("Хуже")
        await client.click_button("Работа")
        await client.click_button(ru.BTN_DONE)
        await client.click_button(ru.BTN_SKIP)
        await client.click_button(ru.BTN_SKIP)
        await client.click_button(ru.BTN_YES)
        for score in ("8", "2", "3", "4", "5"):
            await client.click_button(score)
        await client.click_button(ru.BTN_FINISH)
        await client.send("Шумно в офисе")
    with time_machine.travel(day(6, 9), tick=False):
        await client.send("/morning")
        await client.click_button("Средне")
        await client.send("/morning")
        await client.click_button(ru.BTN_RESTART)  # the first one is abandoned
        await client.click_button(ru.BTN_FINISH)
    with time_machine.travel(day(9, 20), tick=False):
        await client.send("/week")

    filename, text = _document_text(client)
    assert filename == "week-2026-11-09.md"
    assert "# Неделя: 3–9 ноября 2026" in text
    assert "Записи есть за 3 из 7 дн." in text
    assert "после сна: хорошо · физическая энергия: 7/10" in text
    assert "Забирало силы: работа, умственные задачи" in text
    # Each resource separately; no average anywhere.
    assert (
        "Ресурсы (0–10): физический 8 · эмоциональный 2 · социальный 3 · когнитивный 4 · "
        "общее самочувствие 5" in text
    )
    assert "средн" not in text.lower()
    assert "«Шумно в офисе»" in text
    assert "после сна: средне" not in text  # abandoned check-in is not reported
    assert "**Утро**\n- ответов нет" in text
    assert "\n\n**Записи**\n- 19:00 «Шумно в офисе»" in text
    assert text.count("_нет записей_") == 4  # 4, 7, 8, 9 Nov


# --- session brief --------------------------------------------------------------------
async def test_session_brief_and_marking_the_session(make_client: Any, db: Factory) -> None:
    client = make_client()
    with time_machine.travel(day(5), tick=False):
        await client.send("/discuss Границы на работе")
        await client.send("Устала от звонков")
        await client.click_button(ru.BTN_TO_SESSION)
    with time_machine.travel(day(9), tick=False):
        await client.send("/session")
        summary = client.texts()[-1]
        assert "Сводка к сессии" in summary and "3–9 ноября 2026" in summary
        assert "• Границы на работе" in summary and "• Устала от звонков" in summary
        filename, text = _document_text(client)
        assert filename == "session-brief-2026-11-09.md"
        assert "## Что хочу обсудить" in text and "Границы на работе" in text

        await client.click_button(ru.BTN_SESSION_HELD)
        assert client.last_text() == ru.SESSION_ITEMS_QUESTION
        await client.click_button(ru.BTN_ALL_DISCUSSED)
        assert client.last_text() == ru.SESSION_NOTE_PROMPT
        await client.send("Пробовать паузы между звонками")
        assert client.last_text() == ru.SESSION_SAVED

    async with db() as s:
        held = await s.scalar(select(TherapySession))
        items = (await s.scalars(select(DiscussionItem))).all()
    assert held is not None
    assert str(held.session_date) == "2026-11-09"
    assert held.post_session_note == "Пробовать паузы между звонками"
    assert {i.status for i in items} == {DiscussionStatus.DISCUSSED}
    assert {i.session_id for i in items} == {held.id}

    # Next brief: period starts at the last session and shows its note.
    with time_machine.travel(day(12), tick=False):
        await client.send("/session")
        summary = client.texts()[-1]
    assert "9–12 ноября 2026" in summary
    assert "Пробовать паузы между звонками" in summary
    assert ru.SESSION_NO_ITEMS in summary


async def test_session_brief_is_sent_by_the_scheduler(make_client: Any, db: Factory) -> None:
    client = make_client()
    await client.send("/ping")
    async with db() as s:
        user = await s.scalar(select(User))
    assert user is not None
    service = CheckinScheduler(
        AsyncIOScheduler(timezone="UTC"),
        client.bot,
        db,
        extra_senders={PromptKind.SESSION_BRIEF: send_session_brief},
    )
    client.clear()
    await service.fire(user.id, PromptKind.SESSION_BRIEF)
    await service.fire(user.id, PromptKind.SESSION_BRIEF)  # once per day
    assert len(client.documents()) == 1
    assert "Сводка к сессии" in client.texts()[0]


# --- privacy --------------------------------------------------------------------------
async def test_export_all(make_client: Any) -> None:
    client = make_client()
    await client.send("/now")
    await client.click_button("4")
    await client.click_button(ru.BTN_FINISH)
    await client.send("Заметка")
    await client.send("/export_all")
    filename, text = _document_text(client)
    assert filename.startswith("recovery-export-") and filename.endswith(".json")
    data = json.loads(text)
    assert data["user"]["telegram_id"] == 1001
    assert [c["overall_score"] for c in data["checkins"]] == [4]
    assert [m["text"] for m in data["messages"]] == ["Заметка"]
    assert set(data) >= {"checkin_symptoms", "discussion_items", "therapy_sessions"}


async def test_delete_me(make_client: Any, db: Factory) -> None:
    deleted: list[Any] = []

    async def hook(user_id: Any) -> None:
        deleted.append(user_id)

    client = make_client(user_deleted=hook)
    await client.send("/morning")
    await client.click_button("Хорошо")
    await client.send("/delete_me")
    await client.click_button(ru.BTN_DELETE_NO)
    assert await _count(db, User) == 1

    await client.send("/delete_me")
    await client.click_button(ru.BTN_DELETE_YES)
    assert client.last_text() == ru.DELETED
    for model in (User, Checkin, Message, DialogState):
        assert await _count(db, model) == 0, model
    assert len(deleted) == 1


async def test_privacy(make_client: Any) -> None:
    client = make_client()
    await client.send("/privacy")
    assert "/export_all" in client.last_text() and "/delete_me" in client.last_text()
