from __future__ import annotations

import asyncio
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

from .db import Database
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")

@dataclass
class TaskRuntime:
    client: TelegramClient
    new_message_cb: Any
    edited_message_cb: Any | None = None
    source_refs: list[Any] = field(default_factory=list)


class ForwardingEngine:
    """Live forwarding engine with runtime task/session sync + lazy connect."""

    def __init__(self, db: Database, telethon: TelethonService, limit: int) -> None:
        self.db = db
        self.telethon = telethon
        self.semaphore = asyncio.Semaphore(limit)
        self.clients: dict[int, TelegramClient] = {}
        self.client_tasks: dict[int, asyncio.Task[None]] = {}
        self.active_task_count: dict[int, int] = {}
        self.runtimes: dict[int, TaskRuntime] = {}
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        for task in await self.db.get_active_tasks():
            await self._register_task_if_possible(task)

    async def _ensure_user_connected(self, user_id: int) -> TelegramClient | None:
        async with self._lock:
            if user_id in self.clients:
                return self.clients[user_id]
            session = await self.db.get_active_session(user_id)
            if session is None or session["is_blocked"]:
                return None
            try:
                client = await self.telethon.open_saved_client(
                    str(session["encrypted_session_string"])
                )
            except Exception:
                logger.warning(
                    "Could not connect Telegram session for user %s",
                    user_id,
                    exc_info=False,
                )
                return None
            self.clients[user_id] = client
            self.client_tasks[user_id] = asyncio.create_task(
                client.run_until_disconnected()
            )
            self.active_task_count[user_id] = 0
            return client

    async def _maybe_disconnect_user(self, user_id: int) -> None:
        async with self._lock:
            if self.active_task_count.get(user_id, 0) > 0:
                return
            client = self.clients.pop(user_id, None)
            run_task = self.client_tasks.pop(user_id, None)
            self.active_task_count.pop(user_id, None)
            if run_task is not None:
                run_task.cancel()
                await asyncio.gather(run_task, return_exceptions=True)
            if client is not None:
                await client.disconnect()

    async def refresh_user(self, user_id: int) -> None:
        for task in await self.db.get_tasks_for_user(user_id):
            await self.refresh_task(int(task["id"]))

    async def _register_task_if_possible(self, task: Any) -> None:
        if task["is_paused"]:
            return
        await self.refresh_task(int(task["id"]))

    def _entity_reference(self, item: Any) -> str | int | None:
        if not isinstance(item, dict):
            return None
        return item.get("entity_ref") or item.get("label") or item.get("chat_id")

    @staticmethod
    def _load(value: Any) -> Any:
        return value if not isinstance(value, str) else json.loads(value)

    def _apply_text_transforms(self, text: str, settings: dict[str, Any]) -> str | None:
        blacklist = settings.get("blacklist") or []
        whitelist = settings.get("whitelist") or []
        replace = settings.get("replace") or {}

        lowered = text.lower()
        if blacklist and any(word.lower() in lowered for word in blacklist):
            return None
        if whitelist and not any(word.lower() in lowered for word in whitelist):
            return None

        for old, new in replace.items():
            text = text.replace(old, new)

        header = settings.get("header") or ""
        footer = settings.get("footer") or ""
        if header:
            text = f"{header}\n{text}" if text else header
        if footer:
            text = f"{text}\n{footer}" if text else footer
        return text

    async def _apply_watermark(self, client: TelegramClient, message: Any) -> Any:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow not installed, skipping watermark")
            return None
        if not message.photo:
            return None
        try:
            raw = await client.download_media(message, file=bytes)
            image = Image.open(io.BytesIO(raw)).convert("RGBA")
            overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
            draw = ImageDraw.Draw(overlay)
            text = "Dealskoti"
            try:
                font = ImageFont.truetype(
                    "DejaVuSans-Bold.ttf", size=max(18, image.width // 20)
                )
            except Exception:
                font = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            position = (image.width - text_w - 20, image.height - text_h - 20)
            draw.text(position, text, font=font, fill=(255, 255, 255, 160))
            watermarked = Image.alpha_composite(image, overlay).convert("RGB")
            output = io.BytesIO()
            watermarked.save(output, format="JPEG", quality=90)
            output.seek(0)
            output.name = "watermarked.jpg"
            return output
        except Exception:
            logger.warning("Watermarking failed, sending original media", exc_info=False)
            return None

    async def _register_task(self, client: TelegramClient, task: Any) -> None:
        task_id = int(task["id"])
        sources = self._load(task["sources"])
        destinations = self._load(task["destinations"])

        source_refs = [
            ref for item in sources if (ref := self._entity_reference(item)) is not None
        ]
        destination_refs = [
            ref for item in destinations if (ref := self._entity_reference(item)) is not None
        ]
        if not source_refs or not destination_refs:
            return

        async def on_new_message(event: events.NewMessage.Event) -> None:
            async with self.semaphore:
                if not await self.db.task_can_forward(task_id):
                    return
                current = await self.db.get_task(task_id)
                if current is None:
                    return
                live_settings = self._load(current["settings"] or {})

                user_filter = live_settings.get("user_filter") or []
                if user_filter and event.sender_id not in user_filter:
                    return

                message = event.message
                text = message.raw_text or ""
                if text:
                    transformed = self._apply_text_transforms(text, live_settings)
                    if transformed is None:
                        return
                else:
                    transformed = None

                if not await self.db.record_forwarded_message(task_id):
                    logger.info("Daily forwarding limit reached for task %s", task_id)
                    return

                watermark_file = None
                if live_settings.get("watermark") and message.photo:
                    watermark_file = await self._apply_watermark(client, message)

                for destination in destination_refs:
                    dest_msg = await self._copy_with_retry(
                        client, message, destination, transformed, watermark_file
                    )
                    if dest_msg is not None:
                        await self.db.save_message_map(task_id, message.id, dest_msg.id)
                        if live_settings.get("auto_delete_seconds"):
                            asyncio.create_task(
                                self._auto_delete_later(
                                    client,
                                    destination,
                                    dest_msg.id,
                                    int(live_settings["auto_delete_seconds"]),
                                )
                            )

        async def on_edited_message(event: events.MessageEdited.Event) -> None:
            current = await self.db.get_task(task_id)
            if current is None:
                return
            live_settings = self._load(current["settings"] or {})
            if not live_settings.get("edit_sync"):
                return
            message = event.message
            text = message.raw_text or ""
            transformed = self._apply_text_transforms(text, live_settings) if text else None
            if transformed is None and text:
                return
            mapped_ids = await self.db.get_mapped_destination_messages(task_id, message.id)
            for destination in destination_refs:
                for dest_id in mapped_ids:
                    try:
                        await client.edit_message(destination, dest_id, transformed or text)
                    except (RPCError, OSError):
                        logger.warning(
                            "Edit-sync failed for task %s message %s",
                            task_id,
                            message.id,
                            exc_info=False,
                        )

        client.add_event_handler(on_new_message, events.NewMessage(chats=source_refs))
        client.add_event_handler(
            on_edited_message, events.MessageEdited(chats=source_refs)
        )
        self.runtimes[task_id] = TaskRuntime(
            client=client,
            new_message_cb=on_new_message,
            edited_message_cb=on_edited_message,
            source_refs=source_refs,
        )

    async def _auto_delete_later(
        self, client: TelegramClient, destination: Any, message_id: int, delay_seconds: int
    ) -> None:
        await asyncio.sleep(delay_seconds)
        try:
            await client.delete_messages(destination, message_id)
        except (RPCError, OSError):
            logger.warning("Auto-delete failed for message %s", message_id, exc_info=False)

    async def refresh_task(self, task_id: int) -> None:
        await self.remove_task(task_id)
        task = await self.db.get_task(task_id)
        if task is None or task["is_paused"]:
            return
        user_id = int(task["user_id"])
        client = await self._ensure_user_connected(user_id)
        if client is None:
            return
        try:
            await self._register_task(client, task)
            if task_id in self.runtimes:
                self.active_task_count[user_id] = (
                    self.active_task_count.get(user_id, 0) + 1
                )
        except Exception:
            logger.warning("Could not register forwarding task %s", task_id, exc_info=False)

    async def remove_task(self, task_id: int) -> None:
        runtime = self.runtimes.pop(task_id, None)
        if runtime is None:
            return
        runtime.client.remove_event_handler(runtime.new_message_cb)
        if runtime.edited_message_cb is not None:
            runtime.client.remove_event_handler(runtime.edited_message_cb)
        for user_id, client in list(self.clients.items()):
            if client is runtime.client:
                self.active_task_count[user_id] = max(
                    0, self.active_task_count.get(user_id, 1) - 1
                )
                await self._maybe_disconnect_user(user_id)
                break

    async def remove_user(self, user_id: int) -> None:
        for task_id, runtime in list(self.runtimes.items()):
            if self.clients.get(user_id) is runtime.client:
                await self.remove_task(task_id)
        await self._maybe_disconnect_user(user_id)

    async def _copy_with_retry(
        self,
        client: TelegramClient,
        message: Any,
        destination: str | int,
        transformed_text: str | None,
        watermark_file: Any,
    ) -> Any:
        while not self._stop.is_set():
            try:
                if watermark_file is not None:
                    watermark_file.seek(0)
                    return await client.send_file(
                        destination, watermark_file, caption=transformed_text
                    )
                if transformed_text is not None:
                    return await client.send_message(
                        destination, transformed_text, file=message.media or None
                    )
                return await client.send_message(destination, message)
            except FloodWaitError as exc:
                logger.warning("FloodWait while forwarding: %s seconds", exc.seconds)
                # Safely wait for either the timeout or the engine to stop
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=exc.seconds)
                except asyncio.TimeoutError:
                    pass
            except (RPCError, OSError):
                logger.warning("Forwarding failed for one destination", exc_info=False)
                return None
        return None

    async def run_until_stopped(self) -> None:
        await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        for user_id in list(self.clients):
            await self.remove_user(user_id)
