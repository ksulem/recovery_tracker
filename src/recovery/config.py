"""Application settings, read from environment variables (and .env for local runs).

Every variable is prefixed with ``RECOVERY_`` (e.g. ``RECOVERY_BOT_TOKEN``).
The VM is shared with watering-system, whose variables are named
``BOT_TOKEN``, ``OWNER_USER_ID``, ``DATABASE_URL``… — and docker compose lets
exported shell variables override a project's .env. The prefix makes it
impossible to pick up the watering bot's token or database by accident.
"""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "RECOVERY_"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, env_file=".env", extra="ignore")

    bot_token: SecretStr
    owner_user_id: int
    database_url: SecretStr

    log_level: str = "INFO"
    health_host: str = "0.0.0.0"
    health_port: int = 8080

    default_timezone: str = "UTC"

    # Shown in the crisis message. 112 works across Europe, Georgia, Armenia, Russia.
    emergency_number: str = "112"

    # Connection budget: the shared Postgres server allows 40 connections in total.
    db_pool_size: int = 2
    db_max_overflow: int = 2

    # Scheduler
    reminder_delay_minutes: int = 120
    catchup_window_minutes: int = 120

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment
