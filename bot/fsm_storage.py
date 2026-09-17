"""
PostgreSQL-backed FSM storage.

WHY THIS EXISTS
aiogram's default storage keeps a user's position in a multi-step flow in
memory. Railway restarts the bot on every deploy, so that memory was wiped
several times a day — and anyone halfway through /connect, a new task, a date
range or a payment proof was dropped mid-sentence and answered with
"Unknown command", which reads exactly like a broken bot.

Keeping the same information in Postgres (which already survives restarts)
means a deploy is invisible to the user: they send the next message and the
flow carries on.

DESIGN NOTES
  * Nothing is cached in memory. A cache would be faster but would go stale
    the moment a second process existed, and correctness matters far more
    here than the few milliseconds saved.
  * Every method degrades to "no state" on a database error rather than
    raising. A storage failure should mean one lost prompt, never a crashed
    update.
  * Rows are pruned after two days by a scheduled job — nobody resumes a
    two-day-old prompt, and the table would otherwise only grow.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

from .db import Database

logger = logging.getLogger("dealskoti.fsm")


class PostgresStorage(BaseStorage):
    """Stores FSM state in the bot's existing Postgres database."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _key(key: StorageKey) -> str:
        """One string per conversation.

        Built from the same parts aiogram uses, so two chats — or the same
        user in a group and in DM — never share a flow.
        """
        return ":".join(
            str(part) for part in (
                key.bot_id, key.chat_id, key.user_id,
                key.thread_id or 0, key.destiny or "default",
            )
        )

    async def set_state(self, key: StorageKey, state: str | State | None = None) -> None:
        value = state.state if isinstance(state, State) else state
        try:
            await self.db.fsm_set_state(self._key(key), value)
        except Exception as exc:
            logger.warning("Could not save FSM state: %s", exc)

    async def get_state(self, key: StorageKey) -> str | None:
        try:
            state, _data = await self.db.fsm_get(self._key(key))
            return state
        except Exception as exc:
            logger.warning("Could not read FSM state: %s", exc)
            return None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        try:
            await self.db.fsm_set_data(self._key(key), dict(data or {}))
        except Exception as exc:
            logger.warning("Could not save FSM data: %s", exc)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        try:
            _state, data = await self.db.fsm_get(self._key(key))
            return data
        except Exception as exc:
            logger.warning("Could not read FSM data: %s", exc)
            return {}

    async def update_data(
        self, key: StorageKey, data: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Read-modify-write.

        Not atomic, and deliberately left that way: a single user cannot send
        two messages at once, so the only way to race here is a callback and a
        message arriving together — in which case the last writer winning is
        the behaviour aiogram's own storages have too.
        """
        current = await self.get_data(key)
        current.update(dict(data or {}))
        await self.set_data(key, current)
        return current

    async def close(self) -> None:
        """Nothing to close — the database pool is owned by the caller."""
        return None
