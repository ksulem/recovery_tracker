import logging
from typing import Any

import pytest
from aiogram import Dispatcher
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.config import Settings
from recovery.db.models import User
from recovery.logging_setup import REDACTED, RedactingFilter, RedactingFormatter, scrub
from recovery.main import ForeignWebhookError, ensure_polling_is_safe
from tests.tg import STRANGER_ID, TgClient


async def test_owner_gets_pong(make_client: Any) -> None:
    client = make_client()
    await client.send("/ping")
    assert client.last_text() == "pong"


async def test_stranger_gets_no_reply_and_nothing_is_stored(
    make_client: Any, db: async_sessionmaker[AsyncSession]
) -> None:
    client = make_client(user_id=STRANGER_ID)
    await client.send("/start")
    await client.send("/ping")
    await client.click("st:morning:go")
    assert client.session.requests == []
    async with db() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0


def _recovery_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECOVERY_BOT_TOKEN", "1:recovery")
    monkeypatch.setenv("RECOVERY_OWNER_USER_ID", "1")
    monkeypatch.setenv("RECOVERY_DATABASE_URL", "postgresql+asyncpg://u:p@h/recovery")


def test_settings_reject_unknown_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    _recovery_env(monkeypatch)
    monkeypatch.setenv("RECOVERY_DEFAULT_TIMEZONE", "Europe/Tbilisi")  # real name: Asia/Tbilisi
    with pytest.raises(ValueError, match="unknown timezone"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_ignore_watering_system_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    # The watering-system stack on the same VM uses unprefixed names.
    monkeypatch.setenv("BOT_TOKEN", "9:watering")
    monkeypatch.setenv("OWNER_USER_ID", "999")
    monkeypatch.setenv("DATABASE_URL", "postgresql://plantuser:x@timescaledb/plantmonitor")
    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]

    _recovery_env(monkeypatch)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.bot_token.get_secret_value() == "1:recovery"
    assert settings.owner_user_id == 1
    assert "plantmonitor" not in settings.database_url.get_secret_value()


async def test_refuses_to_start_on_a_token_with_a_webhook() -> None:
    client = TgClient(Dispatcher())
    client.session.webhook_url = "https://wateringkl.org/webhook"
    with pytest.raises(ForeignWebhookError):
        await ensure_polling_is_safe(client.bot)
    sent = [type(r).__name__ for r in client.session.requests]
    assert sent == ["GetWebhookInfo"]  # never DeleteWebhook


async def test_starts_on_a_token_without_a_webhook() -> None:
    client = TgClient(Dispatcher())
    await ensure_polling_is_safe(client.bot)


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
