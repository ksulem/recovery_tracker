from datetime import UTC, date, datetime

import pytest
from aiogram.fsm.storage.base import StorageKey
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.conversation.fsm_storage import PostgresStorage
from recovery.db.models import Checkin, CheckinSymptom, DialogState, Message, User
from recovery.domain import CheckinStatus, CheckinType, InputType, Symptom

Factory = async_sessionmaker[AsyncSession]


async def _user(db: Factory, tz: str = "Asia/Tbilisi") -> User:
    async with db() as s, s.begin():
        user = User(telegram_id=1001, timezone=tz)
        s.add(user)
    return user


async def test_local_columns_follow_the_snapshotted_timezone(db: Factory) -> None:
    user = await _user(db)
    async with db() as s, s.begin():
        # 21:30 UTC on Nov 7 is 01:30 on Nov 8 in Tbilisi (UTC+4).
        c = Checkin(
            user_id=user.id,
            checkin_type=CheckinType.EVENING,
            tz_name="Asia/Tbilisi",
            created_at_utc=datetime(2026, 11, 7, 21, 30, tzinfo=UTC),
        )
        s.add(c)
    async with db() as s:
        row = await s.get(Checkin, c.id)
        assert row is not None
        assert row.local_date == date(2026, 11, 8)
        assert row.local_ts == datetime(2026, 11, 8, 1, 30)
        assert row.created_at_utc == datetime(2026, 11, 7, 21, 30, tzinfo=UTC)


async def test_dst_change_is_handled_by_postgres(db: Factory) -> None:
    user = await _user(db, "Europe/Berlin")
    async with db() as s, s.begin():
        m = Message(
            user_id=user.id,
            input_type=InputType.TEXT,
            text="x",
            tz_name="Europe/Berlin",
            # 01:30 UTC on 2026-03-29 is after the spring-forward jump (CEST, UTC+2).
            created_at_utc=datetime(2026, 3, 29, 1, 30, tzinfo=UTC),
        )
        s.add(m)
    async with db() as s:
        local_ts = await s.scalar(select(Message.local_ts).where(Message.id == m.id))
    assert local_ts == datetime(2026, 3, 29, 3, 30)


async def test_scores_out_of_range_are_rejected(db: Factory) -> None:
    user = await _user(db)
    with pytest.raises(IntegrityError, match="physical_score_range"):
        async with db() as s, s.begin():
            s.add(
                Checkin(
                    user_id=user.id,
                    checkin_type=CheckinType.MORNING,
                    tz_name="UTC",
                    physical_score=11,
                )
            )


async def test_unknown_vocabulary_is_rejected(db: Factory) -> None:
    user = await _user(db)
    with pytest.raises(IntegrityError, match="sleep_quality_valid"):
        async with db() as s, s.begin():
            s.add(
                Checkin(
                    user_id=user.id,
                    checkin_type=CheckinType.MORNING,
                    tz_name="UTC",
                    sleep_quality="excellent",
                )
            )


async def test_one_live_morning_checkin_per_day(db: Factory) -> None:
    user = await _user(db)
    at = datetime(2026, 11, 7, 6, 0, tzinfo=UTC)

    def morning(status: str = CheckinStatus.IN_PROGRESS) -> Checkin:
        return Checkin(
            user_id=user.id,
            checkin_type=CheckinType.MORNING,
            status=status,
            tz_name="UTC",
            created_at_utc=at,
        )

    async with db() as s, s.begin():
        first = morning()
        s.add(first)
    with pytest.raises(IntegrityError, match="uq_checkins_daily"):
        async with db() as s, s.begin():
            s.add(morning())
    # "Start over": abandon the old one, a new one is allowed.
    async with db() as s, s.begin():
        row = await s.get(Checkin, first.id)
        assert row is not None
        row.status = CheckinStatus.ABANDONED
        s.add(morning())
    # Ad-hoc check-ins are unlimited.
    async with db() as s, s.begin():
        for _ in range(3):
            s.add(
                Checkin(
                    user_id=user.id,
                    checkin_type=CheckinType.AD_HOC,
                    tz_name="UTC",
                    created_at_utc=at,
                )
            )


async def test_skipped_scores_stay_null(db: Factory) -> None:
    user = await _user(db)
    async with db() as s, s.begin():
        c = Checkin(
            user_id=user.id,
            checkin_type=CheckinType.MORNING,
            tz_name="UTC",
            question_status={"phys": "skipped"},
        )
        s.add(c)
    async with db() as s:
        row = await s.get(Checkin, c.id)
        assert row is not None
        assert row.physical_score is None
        assert row.question_status == {"phys": "skipped"}


async def test_deleting_the_user_removes_everything(db: Factory) -> None:
    user = await _user(db)
    async with db() as s, s.begin():
        c = Checkin(user_id=user.id, checkin_type=CheckinType.EVENING, tz_name="UTC")
        s.add(c)
        await s.flush()
        s.add(CheckinSymptom(checkin_id=c.id, symptom=Symptom.IRRITABILITY))
        s.add(Message(user_id=user.id, input_type=InputType.TEXT, text="x", tz_name="UTC"))
    async with db() as s, s.begin():
        row = await s.get(User, user.id)
        await s.delete(row)
    async with db() as s:
        for model in (Checkin, CheckinSymptom, Message):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


async def test_fsm_storage_survives_a_new_storage_instance(db: Factory) -> None:
    key = StorageKey(bot_id=42, chat_id=1001, user_id=1001)
    storage = PostgresStorage(db)
    assert await storage.get_state(key) is None
    assert await storage.get_data(key) == {}

    await storage.set_state(key, "Checkin:active")
    await storage.set_data(key, {"checkin_id": "abc", "question": "phys"})
    await storage.update_data(key, {"question": "sleep"})

    restarted = PostgresStorage(db)  # e.g. after a redeploy
    assert await restarted.get_state(key) == "Checkin:active"
    assert await restarted.get_data(key) == {"checkin_id": "abc", "question": "sleep"}

    await restarted.set_state(key, None)
    assert await restarted.get_state(key) is None
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(DialogState)) == 1
