"""aiogram FSM storage on the ``dialog_state`` table.

The default MemoryStorage loses the conversation on every restart; with this
storage a half-finished check-in survives a redeploy (spec §13: "данные не
теряются при перезапуске"). Data must be JSON-serialisable — handlers only
store ids and small primitives.
"""

from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import (
    BaseStorage,
    DefaultKeyBuilder,
    KeyBuilder,
    StateType,
    StorageKey,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from recovery.clock import utcnow
from recovery.db.models import DialogState


class PostgresStorage(BaseStorage):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        key_builder: KeyBuilder | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.key_builder = key_builder or DefaultKeyBuilder(with_bot_id=True, with_destiny=True)

    def _key(self, key: StorageKey) -> str:
        return self.key_builder.build(key)

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        stmt = insert(DialogState).values(key=self._key(key), state=value, updated_at_utc=utcnow())
        stmt = stmt.on_conflict_do_update(
            index_elements=[DialogState.key],
            set_={"state": stmt.excluded.state, "updated_at_utc": stmt.excluded.updated_at_utc},
        )
        async with self.session_factory() as session, session.begin():
            await session.execute(stmt)

    async def get_state(self, key: StorageKey) -> str | None:
        async with self.session_factory() as session:
            return await session.scalar(
                select(DialogState.state).where(DialogState.key == self._key(key))
            )

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        payload = dict(data)
        stmt = insert(DialogState).values(key=self._key(key), data=payload, updated_at_utc=utcnow())
        stmt = stmt.on_conflict_do_update(
            index_elements=[DialogState.key],
            set_={"data": stmt.excluded.data, "updated_at_utc": stmt.excluded.updated_at_utc},
        )
        async with self.session_factory() as session, session.begin():
            await session.execute(stmt)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self.session_factory() as session:
            data = await session.scalar(
                select(DialogState.data).where(DialogState.key == self._key(key))
            )
        return dict(data) if data else {}

    async def close(self) -> None:
        return None
