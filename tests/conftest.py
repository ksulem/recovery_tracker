"""Shared fixtures.

Database tests run against a real PostgreSQL 16 given by ``TEST_DATABASE_URL``
(an empty database you don't mind being wiped). Without it they are skipped.
The schema is created by running the real Alembic migrations, so migrations
are tested on every run.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from recovery.db.base import Base
from recovery.db.engine import make_engine, make_session_factory

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "alembic"))
    cfg.attributes["database_url"] = url
    return cfg


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    # Fresh schema, then the real migrations. Alembic's env.py runs its own
    # event loop, so do it in a worker thread, away from pytest-asyncio's loop.
    with ThreadPoolExecutor(1) as pool:
        pool.submit(_reset_schema, url).result()
        pool.submit(command.upgrade, _alembic_config(url), "head").result()
    yield url


def _reset_schema(url: str) -> None:
    async def reset() -> None:
        engine = make_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())


@pytest.fixture(scope="session")
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = make_engine(db_url, pool_size=4, max_overflow=4)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory on an empty database (all tables truncated)."""
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    return make_session_factory(engine)


@pytest.fixture
def make_client(db: async_sessionmaker[AsyncSession]):  # type: ignore[no-untyped-def]
    """Build a TgClient on a fresh dispatcher (same DB) — calling it twice
    simulates a bot restart."""
    from recovery.bot_factory import build_dispatcher
    from tests.tg import OWNER_ID, TgClient

    def _make(user_id: int = OWNER_ID, **kwargs):  # type: ignore[no-untyped-def]
        dp = build_dispatcher(owner_id=OWNER_ID, session_factory=db, **kwargs)
        return TgClient(dp, user_id=user_id)

    return _make
