from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

from .db import Database
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")


class ForwardingEngine:
    """Live forwarding engine with runtime task/session synchronization."""

    def __init__(self, db: Database, telethon: TelethonService, limit: int) -> None:
        self.db = db
        self.telethon = telethon
        self.semaphore = asyncio.Semaphore(limit)
        self.clients: dict[int, TelegramClient] = {}
        self.client_tasks: dict[int, asyncio.Task[None]] = {}
        self.handlers: dict[int, list[tuple[TelegramClient, Any]]] = {}
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        for session in await self.db.get_active_sessions():
            try:
                await self._open_user(
                    int(session["telegram_user_id"]),
                    str(session["encrypted_session_string"]),
                )
            except Exception:
                logger.warning(
                    "Could not restore Telegram session for user %s",
                    session["telegram_user_id"],
                    exc_info=False,
                )
        for task in await self.db.get_active_tasks():
            await self._register_task_if_possible(task)

    async def _open_user(self, user_id: int, encrypted_session: str) -> None:
        async with self._lock:
            if user_id in self.clients:
                return
            client = await self.telethon.open_saved_client(encrypted_session)
            self.clients[user_id] = client
            self.client_tasks[user_id] = asyncio.create_task(
                client.run_until_disconnected()
            )

    async def refresh_user(self, user_id: int) -> None:
        """Reload one session after connect/disconnect or membership changes."""
        await self.remove_user(user_id)
        session = await self.db.get_active_session(user_id)
        if session is None or session["is_blocked"]:
            return
        try:
            await self._open_user(
                user_id, str(session["encrypted_session_string"])
            )
            client = self.clients.get(user_id)
            if client is not None:
                for task in await self.db.get_tasks_for_user(user_id):
                    if not task["is_paused"]:
                        await self._register_task(client, task)
        except Exception:
            logger.warning(
                "Could not refresh Telegram session for user %s",
                user_id,
                exc_info=False,
            )

    async def _register_task_if_possible(self, task: Any) -> None:
        user_id = int(task["user_id"])
        if task["is_paused"] or user_id not in self.clients:
            return
        await self.refresh_task(int(task["id"]))

    def _entity_reference(self, item: Any) -> str | int | None:
        if not isinstance(item, dict):
            return None
        return item.get("entity_ref") or item.get("label") or item.get("chat_id")

    async def _register_task(self, client: TelegramClient, task: Any) -> None:
        task_id = int(task["id"])
        sources = (
            task["sources"]
            if isinstance(task["sources"], list)
            else json.loads(task["sources"])
        )
        destinations = (
            task["destinations"]
            if isinstance(task["destinations"], list)
            else json.loads(task["destinations"])
        )
        source_refs = [
            ref
            for item in sources
            if (ref := self._entity_reference(item)) is not None
        ]
        destination_refs = [
            ref
            for item in destinations
            if (ref := self._entity_reference(item)) is not None
        ]
        if not source_refs or not destination_refs:
            return

        async def on_new_message(event: events.NewMessage.Event) -> None:
            async with self.semaphore:
                if not await self.db.task_can_forward(task_id):
                    return
                if not await self.db.record_forwarded_message(task_id):
                    logger.info("Daily forwarding limit reached for task %s", task_id)
                    return
                for destination in destination_refs:
                    await self._copy_with_retry(event.message, destination)

        client.add_event_handler(on_new_message, events.NewMessage(chats=source_refs))
        self.handlers.setdefault(task_id, []).append((client, on_new_message))

    async def refresh_task(self, task_id: int) -> None:
        await self.remove_task(task_id)
        task = await self.db.get_task(task_id)
        if task is None or task["is_paused"]:
            return
        client = self.clients.get(int(task["user_id"]))
        if client is None:
            await self.refresh_user(int(task["user_id"]))
            client = self.clients.get(int(task["user_id"]))
        if client is not None:
            try:
                await self._register_task(client, task)
            except Exception:
                logger.warning(
                    "Could not register forwarding task %s",
                    task_id,
                    exc_info=False,
                )

    async def remove_task(self, task_id: int) -> None:
        for client, callback in self.handlers.pop(task_id, []):
            client.remove_event_handler(callback)

    async def remove_user(self, user_id: int) -> None:
        for task_id, handlers in list(self.handlers.items()):
            remaining: list[tuple[TelegramClient, Any]] = []
            for client, callback in handlers:
                if client is self.clients.get(user_id):
                    client.remove_event_handler(callback)
                else:
                    remaining.append((client, callback))
            if remaining:
                self.handlers[task_id] = remaining
            else:
                self.handlers.pop(task_id, None)
        client = self.clients.pop(user_id, None)
        run_task = self.client_tasks.pop(user_id, None)
        if run_task is not None:
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        if client is not None:
            await client.disconnect()

    async def _copy_with_retry(self, message: Any, destination: str | int) -> None:
        client = message._client
        while not self._stop.is_set():
            try:
                await client.send_message(destination, message)
                return
            except FloodWaitError as exc:
                logger.warning("FloodWait while forwarding: %s seconds", exc.seconds)
                await asyncio.sleep(exc.seconds)
            except (RPCError, OSError):
                logger.warning(
                    "Forwarding failed for one destination", exc_info=False
                )
                return

    async def run_until_stopped(self) -> None:
        await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        for user_id in list(self.clients):
            await self.remove_user(user_id)
