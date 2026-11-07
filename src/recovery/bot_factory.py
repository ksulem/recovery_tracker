"""Builds the dispatcher. Kept separate from ``main`` so tests can build the
same dispatcher and feed it updates without polling Telegram."""

from aiogram import Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.conversation.fsm_storage import PostgresStorage
from recovery.safety import SafetyMiddleware
from recovery.telegram.hooks import (
    SettingsChanged,
    UserDeleted,
    noop_settings_changed,
    noop_user_deleted,
)
from recovery.telegram.middlewares import DbSessionMiddleware, OwnerOnlyMiddleware
from recovery.telegram.routers import checkin, common, notes, privacy, reports, setup


def build_dispatcher(
    *,
    owner_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    default_timezone: str = "UTC",
    storage: BaseStorage | None = None,
    settings_changed: SettingsChanged = noop_settings_changed,
    user_deleted: UserDeleted = noop_user_deleted,
    emergency_number: str = "112",
) -> Dispatcher:
    dp = Dispatcher(
        storage=storage or PostgresStorage(session_factory),
        settings_changed=settings_changed,
        user_deleted=user_deleted,
    )
    # Order matters: drop strangers before anything touches the database.
    dp.update.outer_middleware(OwnerOnlyMiddleware(owner_id))
    dp.update.outer_middleware(DbSessionMiddleware(session_factory, default_timezone))
    # Before any message handler or state filter: a crisis message stops the flow.
    dp.message.outer_middleware(SafetyMiddleware(emergency_number))
    dp.include_router(common.make_router())
    dp.include_router(setup.make_router())
    dp.include_router(checkin.make_router())
    dp.include_router(reports.make_router())
    dp.include_router(privacy.make_router())
    dp.include_router(notes.make_router())  # catch-all for free text: keep last
    return dp
