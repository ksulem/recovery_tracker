"""Callbacks the Telegram layer uses to notify other modules."""

import uuid
from collections.abc import Awaitable, Callable

from recovery.db.models import User

# Called after the owner changed times/timezone/session settings, so the
# scheduler can rebuild its jobs. Injected via dispatcher workflow data.
SettingsChanged = Callable[[User], Awaitable[None]]


async def noop_settings_changed(_: User) -> None:
    return None


# Called after /delete_me removed the owner's data, so scheduled jobs stop.
UserDeleted = Callable[[uuid.UUID], Awaitable[None]]


async def noop_user_deleted(_: uuid.UUID) -> None:
    return None
