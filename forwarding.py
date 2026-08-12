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
    """Real-time message copy worker with bounded concurrency and FloodWait retry."""

    def __init__(self, db: Database, telethon: TelethonService, limit: int) -> None:
        self.db = db
        self.telethon = telethon
        self.semaphore = asyncio.Semaphore(limit)
        self.clients: dict[int, TelegramClient] = {}
        self._stop = asyncio.Event()

    async def start(self) -> None:
        for session in await self.db.get_active_sessions():
            if not session["updates_channel_member"]:
                continue
            try:
                self.clients[int(session["telegram_user_id"])] = (
                    await self.telethon.open_saved_client(session["encrypted_session_string"])
                )
            except Exception:
                logger.warning("Could not restore one Telegram session", exc_info=False)
        for task in await self.db.get_active_tasks():
            client = self.clients.get(int(task["user_id"]))
            if client is not None:
                self._register_task(client, task)

    def _register_task(self, client: TelegramClient, task: Any) -> None:
        sources = task["sources"] if isinstance(task["sources"], list) else json.loads(task["sources"])
        destinations = (
            task["destinations"]
            if isinstance(task["destinations"], list)
            else json.loads(task["destinations"])
        )
        source_ids = [item["chat_id"] for item in sources if isinstance(item, dict)]
        destination_ids = [item["chat_id"] for item in destinations if isinstance(item, dict)]
        if not source_ids or not destination_ids:
            return

        @client.on(events.NewMessage(chats=source_ids))
        async def on_new_message(event: events.NewMessage.Event) -> None:
            async with self.semaphore:
                for destination in destination_ids:
                    await self._copy_with_retry(event.message, destination)

    async def _copy_with_retry(self, message: Any, destination: int) -> None:
        client = message._client
        while not self._stop.is_set():
            try:
                await client.send_message(destination, message)
                return
            except FloodWaitError as exc:
                await asyncio.sleep(exc.seconds)
            except (RPCError, OSError):
                logger.warning("Forwarding failed for one destination", exc_info=False)
                return

    async def run_until_stopped(self) -> None:
        clients = list(self.clients.values())
        if clients:
            await asyncio.gather(*(client.run_until_disconnected() for client in clients))
        else:
            await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.gather(
            *(client.disconnect() for client in self.clients.values()),
            return_exceptions=True,
        )