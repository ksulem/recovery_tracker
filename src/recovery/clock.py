"""Single source of "now". All timestamps are created here, never by the
database, so local dates computed in Python and stored by Postgres agree
(and tests can freeze time)."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_now(tz_name: str) -> datetime:
    return utcnow().astimezone(ZoneInfo(tz_name))


def local_today(tz_name: str) -> date:
    return local_now(tz_name).date()
