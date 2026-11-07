"""Full data export (spec §12.7): everything stored about the owner, as JSON."""

import json
import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from recovery.clock import local_today
from recovery.db.base import Base
from recovery.db.models import (
    Checkin,
    CheckinSymptom,
    Consent,
    DiscussionItem,
    LoadEvent,
    Message,
    RecoveryEvent,
    SafetyEvent,
    ScheduledPrompt,
    TherapySession,
    User,
)

PER_USER = [
    Checkin,
    LoadEvent,
    RecoveryEvent,
    Message,
    TherapySession,
    DiscussionItem,
    ScheduledPrompt,
    Consent,
    SafetyEvent,
]


def _value(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    return v


def _row(obj: Base) -> dict[str, Any]:
    return {attr.key: _value(getattr(obj, attr.key)) for attr in inspect(obj).mapper.column_attrs}


async def export_json(session: AsyncSession, user: User) -> tuple[str, bytes]:
    data: dict[str, Any] = {"user": _row(user)}
    for model in PER_USER:
        rows = (
            await session.scalars(select(model).where(model.user_id == user.id))  # type: ignore[attr-defined]
        ).all()
        data[model.__tablename__] = [_row(r) for r in rows]
    checkin_ids = [uuid.UUID(c["id"]) for c in data["checkins"]]
    symptoms = (
        (
            await session.scalars(
                select(CheckinSymptom).where(CheckinSymptom.checkin_id.in_(checkin_ids))
            )
        ).all()
        if checkin_ids
        else []
    )
    data["checkin_symptoms"] = [_row(s) for s in symptoms]
    body = json.dumps(data, ensure_ascii=False, indent=2).encode()
    return f"recovery-export-{local_today(user.timezone).isoformat()}.json", body
