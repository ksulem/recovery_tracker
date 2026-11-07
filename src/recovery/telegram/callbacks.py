"""Inline-button payloads. Telegram limits callback data to 64 bytes and the
packer uses ":" as separator, so values are short codes (times as "0930")."""

from aiogram.filters.callback_data import CallbackData


class StartCb(CallbackData, prefix="st"):
    kind: str  # morning | evening | ad_hoc
    op: str  # go | later | resume | restart


class AnswerCb(CallbackData, prefix="qa"):
    c: str  # first 8 hex chars of the check-in id — rejects buttons of old check-ins
    q: str  # question id
    op: str  # set | skip | tog | done | fin
    v: str = ""


class SetupCb(CallbackData, prefix="su"):
    field: str  # tz | morning | evening | sday | stime | quiet
    v: str


class SettingsCb(CallbackData, prefix="sm"):
    item: str  # tz | morning | evening | session | quiet | rem | trans


def encode_time(hhmm: str) -> str:
    return hhmm.replace(":", "")


def short_id(checkin_id: object) -> str:
    return str(checkin_id).replace("-", "")[:8]
