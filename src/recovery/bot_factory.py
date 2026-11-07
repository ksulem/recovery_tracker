"""Builds the dispatcher. Kept separate from ``main`` so tests can build the
same dispatcher and feed it updates without polling Telegram."""

from aiogram import Dispatcher

from recovery.telegram.middlewares import OwnerOnlyMiddleware
from recovery.telegram.routers import common


def build_dispatcher(*, owner_id: int) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(OwnerOnlyMiddleware(owner_id))
    dp.include_router(common.make_router())
    return dp
