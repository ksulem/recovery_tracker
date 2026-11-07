"""Callbacks the Telegram layer uses to notify other modules."""

from collections.abc import Awaitable, Callable

from recovery.db.models import User

# Called after the owner changed times/timezone/session settings, so the
# scheduler can rebuild its jobs. Injected via dispatcher workflow data.
SettingsChanged = Callable[[User], Awaitable[None]]


async def noop_settings_changed(_: User) -> None:
    return None
