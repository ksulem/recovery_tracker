"""Builds the dispatcher. Kept separate from ``main`` so tests can build the
same dispatcher and feed it updates without polling Telegram."""

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.conversation.fsm_storage import PostgresStorage
from recovery.telegram.hooks import SettingsChanged, noop_settings_changed
from recovery.telegram.middlewares import DbSessionMiddleware, OwnerOnlyMiddleware
from recovery.telegram.routers import checkin, common, setup


def build_dispatcher(
    *,
    owner_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    default_timezone: str = "UTC",
    storage: BaseStorage | None = None,
    settings_changed: SettingsChanged = noop_settings_changed,
) -> Dispatcher:
    dp = Dispatcher(
        storage=storage or PostgresStorage(session_factory),
        settings_changed=settings_changed,
    )
    # Order matters: drop strangers before anything touches the database.
    dp.update.outer_middleware(OwnerOnlyMiddleware(owner_id))
    dp.update.outer_middleware(DbSessionMiddleware(session_factory, default_timezone))
    # Commands first, then state-specific text handlers inside each router.
    dp.include_router(common.make_router())
    dp.include_router(setup.make_router())
    dp.include_router(checkin.make_router())
    return dp
