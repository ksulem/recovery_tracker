"""Async engine and session factory.

Every connection runs with ``timezone=UTC`` so TIMESTAMPTZ values are read
and displayed as UTC regardless of server defaults.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

logger = logging.getLogger(__name__)


def make_engine(url: str, *, pool_size: int = 2, max_overflow: int = 2) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        # The shared Postgres container can be restarted by the other project.
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC", "application_name": "recovery"}},
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


async def wait_for_db(engine: AsyncEngine, *, attempts: int = 30, delay: float = 2.0) -> None:
    """Block until the database answers; the DB container may start after us."""
    for attempt in range(1, attempts + 1):
        if await ping(engine):
            return
        logger.warning("database not reachable (attempt %d/%d)", attempt, attempts)
        await asyncio.sleep(delay)
    raise RuntimeError("database is not reachable")
