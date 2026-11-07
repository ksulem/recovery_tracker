import logging

import pytest

from recovery.bot_factory import build_dispatcher
from recovery.config import Settings
from recovery.logging_setup import REDACTED, RedactingFilter, RedactingFormatter, scrub
from tests.tg import OWNER_ID, STRANGER_ID, TgClient


async def test_owner_gets_pong() -> None:
    client = TgClient(build_dispatcher(owner_id=OWNER_ID))
    await client.send("/ping")
    assert client.last_text() == "pong"


async def test_stranger_gets_no_reply() -> None:
    client = TgClient(build_dispatcher(owner_id=OWNER_ID), user_id=STRANGER_ID)
    await client.send("/ping")
    assert client.session.requests == []


def test_settings_reject_unknown_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("OWNER_USER_ID", "1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Europe/Tbilisi")  # real name is Asia/Tbilisi
    with pytest.raises(ValueError, match="unknown timezone"):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw",
    [
        "1 validation error\n  input_value='мне плохо сегодня', input_type=str",
        "payload={'text': 'неприятный разговор с коллегой'}",
        'transcript="три часа сложной работы"',
    ],
)
def test_scrub_removes_content(raw: str) -> None:
    out = scrub(raw)
    assert REDACTED in out
    for word in ("плохо", "разговор", "работы"):
        assert word not in out


def test_log_records_never_carry_message_text(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.redaction")
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(message)s %(text)s"))
    record = logger.makeRecord(
        "x",
        logging.INFO,
        __file__,
        1,
        "saved message id=%s",
        (5,),
        None,
        extra={"text": "секрет"},
    )
    handler.filter(record)
    formatted = handler.format(record)
    assert "секрет" not in formatted
    assert "id=5" in formatted
