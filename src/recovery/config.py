"""Application settings, read from environment variables (and .env for local runs)."""

from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: SecretStr
    owner_user_id: int
    database_url: SecretStr

    log_level: str = "INFO"
    health_host: str = "0.0.0.0"
    health_port: int = 8080

    default_timezone: str = "UTC"

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
