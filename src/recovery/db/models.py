"""ORM models.

Time convention (plan §2.1): every row that records *when* something happened
stores the UTC instant (``*_at_utc``), the owner's IANA timezone at write time
(``tz_name``) and two generated columns derived from them by Postgres:
``local_ts`` (wall clock) and ``local_date`` (the calendar day all daily and
weekly aggregates group by). Snapshotting ``tz_name`` keeps old days where they
were if the timezone later changes.

Closed vocabularies are TEXT + CHECK constraints built from ``recovery.domain``.
Skipped answers are NULL — never 0.
"""

import uuid
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from recovery.clock import utcnow
from recovery.db.base import Base
from recovery.domain import (
    CheckinStatus,
    CheckinType,
    Comparison,
    Confidence,
    ConsentKind,
    DayOutlook,
    DiscussionStatus,
    Effect,
    InputType,
    Intensity,
    LoadCategory,
    PartOfDay,
    PleasantDesire,
    PleasantEnergy,
    PromptKind,
    PromptStatus,
    RecoveryActivity,
    SafetySource,
    SleepQuality,
    Source,
    Symptom,
)


def one_of(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name or f"{column}_valid")


def between(column: str, low: int, high: int) -> CheckConstraint:
    return CheckConstraint(f"{column} BETWEEN {low} AND {high}", name=f"{column}_range")


def utc_column(*, nullable: bool = False, default: bool = False) -> Mapped[Any]:
    return mapped_column(
        DateTime(timezone=True), nullable=nullable, default=utcnow if default else None
    )


def user_fk() -> Mapped[uuid.UUID]:
    # No single-column index: tables that are queried by user have a composite
    # (user_id, local_date) index, and there is only one user.
    return mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)


class LocalTimeMixin:
    """``created_at_utc`` + ``tz_name`` → generated ``local_ts`` / ``local_date``."""

    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    tz_name: Mapped[str] = mapped_column(Text, nullable=False)

    @declared_attr
    def local_ts(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=False),
            Computed("created_at_utc AT TIME ZONE tz_name", persisted=True),
        )

    @declared_attr
    def local_date(cls) -> Mapped[date]:
        return mapped_column(
            Date, Computed("(created_at_utc AT TIME ZONE tz_name)::date", persisted=True)
        )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        between("session_weekday", 0, 6),
        between("session_brief_lead_hours", 0, 48),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False, default="UTC")
    morning_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(9, 30))
    evening_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(21, 0))
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    quiet_hours_start: Mapped[time | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time)
    store_transcripts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    # Psychologist session: 0 = Monday … 6 = Sunday, local time.
    session_weekday: Mapped[int | None] = mapped_column(SmallInteger)
    session_time: Mapped[time | None] = mapped_column(Time)
    session_brief_lead_hours: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default=text("3")
    )
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at_utc: Mapped[datetime] = utc_column(default=True)


class Checkin(LocalTimeMixin, Base):
    __tablename__ = "checkins"
    __table_args__ = (
        one_of("checkin_type", CheckinType),
        one_of("status", CheckinStatus),
        between("physical_score", 0, 10),
        between("emotional_score", 0, 10),
        between("social_score", 0, 10),
        between("cognitive_score", 0, 10),
        between("overall_score", 0, 10),
        between("interest_score", 0, 5),
        one_of("sleep_quality", SleepQuality),
        one_of("comparison_to_morning", Comparison),
        one_of("day_outlook", DayOutlook),
        one_of("pleasant_desire", PleasantDesire),
        one_of("pleasant_energy", PleasantEnergy),
        # At most one live morning and one live evening check-in per local day;
        # "start over" abandons the old one. Ad-hoc check-ins are unlimited.
        Index(
            "uq_checkins_daily",
            "user_id",
            "checkin_type",
            "local_date",
            unique=True,
            postgresql_where=text("status <> 'abandoned' AND checkin_type <> 'ad_hoc'"),
        ),
        Index("ix_checkins_user_date", "user_id", "local_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    checkin_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=CheckinStatus.IN_PROGRESS)
    completed_at_utc: Mapped[datetime | None] = utc_column(nullable=True)

    physical_score: Mapped[int | None] = mapped_column(SmallInteger)
    emotional_score: Mapped[int | None] = mapped_column(SmallInteger)
    social_score: Mapped[int | None] = mapped_column(SmallInteger)
    cognitive_score: Mapped[int | None] = mapped_column(SmallInteger)
    overall_score: Mapped[int | None] = mapped_column(SmallInteger)
    interest_score: Mapped[int | None] = mapped_column(SmallInteger)

    sleep_quality: Mapped[str | None] = mapped_column(Text)
    comparison_to_morning: Mapped[str | None] = mapped_column(Text)
    day_outlook: Mapped[str | None] = mapped_column(Text)
    pleasant_desire: Mapped[str | None] = mapped_column(Text)
    pleasant_energy: Mapped[str | None] = mapped_column(Text)
    expected_heavy_load: Mapped[str | None] = mapped_column(Text)
    tomorrow_note: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    # question id -> "answered" | "skipped". Distinguishes "skipped" from
    # "never asked" (both leave the value NULL) and drives resume.
    question_status: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class CheckinSymptom(Base):
    __tablename__ = "checkin_symptoms"
    __table_args__ = (one_of("symptom", Symptom), one_of("source", Source))

    checkin_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("checkins.id", ondelete="CASCADE"), primary_key=True
    )
    symptom: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, default=Source.BUTTON)


class Message(LocalTimeMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        one_of("input_type", InputType),
        Index("ix_messages_user_date", "user_id", "local_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    input_type: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    transcript_status: Mapped[str | None] = mapped_column(Text)
    raw_file_reference: Mapped[str | None] = mapped_column(Text)
    checkin_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("checkins.id", ondelete="SET NULL")
    )
    purge_after_utc: Mapped[datetime | None] = utc_column(nullable=True)


class LoadEvent(LocalTimeMixin, Base):
    __tablename__ = "load_events"
    __table_args__ = (
        one_of("category", LoadCategory),
        one_of("intensity", Intensity),
        one_of("occurred_part_of_day", PartOfDay),
        one_of("source", Source),
        one_of("confidence", Confidence),
        CheckConstraint("duration_minutes > 0", name="duration_positive"),
        Index("ix_load_events_user_date", "user_id", "local_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    checkin_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("checkins.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    intensity: Mapped[str] = mapped_column(Text, nullable=False, default=Intensity.UNKNOWN)
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    occurred_at_utc: Mapped[datetime | None] = utc_column(nullable=True)
    occurred_part_of_day: Mapped[str] = mapped_column(
        Text, nullable=False, default=PartOfDay.UNKNOWN
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str | None] = mapped_column(Text)


class RecoveryEvent(LocalTimeMixin, Base):
    __tablename__ = "recovery_events"
    __table_args__ = (
        one_of("activity", RecoveryActivity),
        between("enjoyment", 0, 5),
        one_of("immediate_effect", Effect),
        one_of("next_morning_effect", Effect),
        one_of("source", Source),
        one_of("confidence", Confidence),
        Index("ix_recovery_events_user_date", "user_id", "local_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    checkin_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("checkins.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )
    activity: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    enjoyment: Mapped[int | None] = mapped_column(SmallInteger)
    immediate_effect: Mapped[str] = mapped_column(Text, nullable=False, default=Effect.UNKNOWN)
    # Filled from the next morning's check-in — never guessed.
    next_morning_effect: Mapped[str] = mapped_column(Text, nullable=False, default=Effect.UNKNOWN)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str | None] = mapped_column(Text)


class DialogState(Base):
    """aiogram FSM storage, so a conversation survives restarts."""

    __tablename__ = "dialog_state"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    state: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    updated_at_utc: Mapped[datetime] = utc_column(default=True)


class ScheduledPrompt(Base):
    """One row per prompt per local day: guards against double sends and
    feeds "missed check-ins" in reports."""

    __tablename__ = "scheduled_prompts"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "local_date", name="uq_scheduled_prompts_daily"),
        one_of("kind", PromptKind),
        one_of("status", PromptStatus),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=PromptStatus.SENDING)
    created_at_utc: Mapped[datetime] = utc_column(default=True)
    sent_at_utc: Mapped[datetime | None] = utc_column(nullable=True)


class TherapySession(LocalTimeMixin, Base):
    """A held psychologist session. The next brief covers the period since
    the latest ``session_date``."""

    __tablename__ = "therapy_sessions"
    __table_args__ = (Index("ix_therapy_sessions_user_date", "user_id", "session_date"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    session_date: Mapped[date] = mapped_column(Date, nullable=False)
    brief_period_start: Mapped[date | None] = mapped_column(Date)
    brief_period_end: Mapped[date | None] = mapped_column(Date)
    brief_sent_at_utc: Mapped[datetime | None] = utc_column(nullable=True)
    post_session_note: Mapped[str | None] = mapped_column(Text)


class DiscussionItem(LocalTimeMixin, Base):
    """Something marked "обсудить на сессии"."""

    __tablename__ = "discussion_items"
    __table_args__ = (
        one_of("status", DiscussionStatus),
        Index("ix_discussion_items_user_status", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    text: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default=DiscussionStatus.OPEN)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("therapy_sessions.id", ondelete="SET NULL")
    )
    resolved_at_utc: Mapped[datetime | None] = utc_column(nullable=True)


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (one_of("kind", ConsentKind),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at_utc: Mapped[datetime] = utc_column(default=True)
    revoked_at_utc: Mapped[datetime | None] = utc_column(nullable=True)


class SafetyEvent(LocalTimeMixin, Base):
    """Only the fact that the crisis response was shown — never the text."""

    __tablename__ = "safety_events"
    __table_args__ = (one_of("source", SafetySource),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = user_fk()
    source: Mapped[str] = mapped_column(Text, nullable=False)
