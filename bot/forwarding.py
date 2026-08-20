import asyncio
import io
import json
import logging
import re
from contextlib import suppress
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message, MessageMediaPhoto

from .db import Database
from .plans import PLANS
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")

class ForwardingEngine:
    def __init__(self, db: Database, telethon: TelethonService, max_concurrent_tasks: int = 100):
        self.db = db
        self.telethon = telethon
        self.max_concurrent = max(1, max_concurrent_tasks)
        self._send_semaphore = asyncio.Semaphore(self.max_concurrent)
        
        # In-memory stores
        self.clients: dict[int, TelegramClient] = {}  # user_id -> TelegramClient
        self._running = False
        self.edit_map: dict[str, list[tuple[int, int]]] = {}  # "user_id:source_chat:msg_id" -> [(dest_chat, dest_msg_id)]

    async def start(self) -> None:
        """Starts the forwarding engine and connects all valid users."""
        self._running = True
        users = await self.db.list_users(limit=10000)
        bad_sessions = 0
        for user in users:
            user_id = int(user["telegram_user_id"])
            if not user["is_blocked"]:
                before = len(self.clients)
                await self.refresh_user(user_id)
                if len(self.clients) == before and await self.db.has_active_session(user_id):
                    bad_sessions += 1
        logger.info(f"Forwarding Engine started. Active clients: {len(self.clients)}. Invalid sessions cleared: {bad_sessions}")

    async def stop(self) -> None:
        """Stops the engine and safely disconnects all clients."""
        self._running = False
        for user_id in list(self.clients.keys()):
            await self.remove_user(user_id)
        logger.info("Forwarding Engine stopped.")

    async def run_until_stopped(self) -> None:
        while self._running:
            await asyncio.sleep(1)

    # --- CLIENT MANAGEMENT ---

    async def refresh_user(self, user_id: int) -> None:
        """Starts or restarts the TelegramClient for a user to apply new settings/tasks."""
        if not self._running:
            return

        # Ensure we don't have stale clients (prevents duplicate event handlers)
        await self.remove_user(user_id)

        user = await self.db.get_user(user_id)
        if not user or user["is_blocked"]:
            return

        session_string = await self.telethon._get_session_string(user_id)
        if not session_string:
            return

        try:
            client = TelegramClient(StringSession(session_string), self.telethon.api_id, self.telethon.api_hash)
        except (ValueError, TypeError) as exc:
            # Corrupted / not-a-valid-string session — nuke it so user has to /connect again.
            logger.warning(f"Dropping invalid session for user {user_id}: {exc}")
            try:
                await self.telethon.disconnect(user_id)
            except Exception:
                pass
            return

        try:
            await client.connect()
            if not await client.is_user_authorized():
                await self.telethon.disconnect(user_id)
                if client.is_connected():
                    await client.disconnect()
                return

            # Register Event Handlers with weak refs so duplicates from refresh never stack
            client.add_event_handler(
                lambda event: self._on_new_message(event, user_id),
                events.NewMessage()
            )
            client.add_event_handler(
                lambda event: self._on_message_edited(event, user_id),
                events.MessageEdited()
            )

            self.clients[user_id] = client

        except Exception as e:
            logger.error(f"Failed to start forwarding client for user {user_id}: {e}")
            if client.is_connected():
                await client.disconnect()

    async def remove_user(self, user_id: int) -> None:
        """Stops and removes the user's forwarding client."""
        client = self.clients.pop(user_id, None)
        if client:
            client.remove_event_handlers()
            if client.is_connected():
                await client.disconnect()
        # Also drop any edit-map entries belonging to this user
        prefix = f"{user_id}:"
        for k in list(self.edit_map.keys()):
            if k.startswith(prefix):
                self.edit_map.pop(k, None)

    async def refresh_task(self, task_id: int) -> None:
        """Hot-reloads a user's client if a specific task was updated."""
        task = await self.db.get_task(task_id)
        if task:
            await self.refresh_user(int(task["user_id"]))

    async def remove_task(self, task_id: int) -> None:
        pass

    # --- MESSAGE PROCESSING ENGINE ---

    def _clean_text(self, text: str, settings: dict, plan_name: str) -> str:
        """Applies Blacklist, Replace, Header, Footer AND NEW Premium filters."""
        if not text:
            if plan_name in ["silver", "gold", "platinum"]:
                header = settings.get("header", "")
                footer = settings.get("footer", "")
                if header or footer:
                    parts = []
                    if header: parts.append(header)
                    if footer: parts.append(footer)
                    return "\n\n".join(parts)
            return text

        # NEW LAYER: Advanced Text Filters (Premium Features)
        if plan_name in ["gold", "platinum"]:
            if settings.get("remove_links", False):
                # Removes http/https and standard domains
                text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', text)
            
            if settings.get("remove_usernames", False):
                # Removes @usernames
                text = re.sub(r'@[a-zA-Z0-9_]+', '', text)
                
            replacements = settings.get("replace", {})
            if isinstance(replacements, dict):
                for old_word, new_word in replacements.items():
                    if old_word:  
                        text = text.replace(old_word, new_word)

        if plan_name in ["silver", "gold", "platinum"]:
            header = settings.get("header", "")
            footer = settings.get("footer", "")

            parts = []
            if header:
                parts.append(header)
            
            # Mono text conversion layer
            if settings.get("mono_text", False) and plan_name in ["gold", "platinum"]:
                parts.append(f"`{text}`")
            else:
                parts.append(text)
                
            if footer:
                parts.append(footer)

            text = "\n\n".join(parts)

        return text

    async def _on_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
        """Triggered when the user's account receives a new message in any chat."""
        message: Message = event.message
        chat_id = event.chat_id

        user = await self.db.get_user(user_id)
        if not user or user["is_blocked"]:
            return

        tasks = await self.db.list_tasks(user_id)
        if not tasks:
            return

        plan_name = str(user["plan"] or "free")
        plan = PLANS.get(plan_name, PLANS["free"])

        client = self.clients.get(user_id)
        if not client:
            return

        for task in tasks:
            if task["is_paused"]:
                continue

            sources = json.loads(task["sources"] or "[]")
            source_ids = [s.get("id") for s in sources if isinstance(s, dict)]
            if chat_id not in source_ids:
                continue

            settings = json.loads(task["settings"] or "{}")

            # --- FILTERS ---
            if plan_name == "platinum":
                allowed_senders = settings.get("user_filter", [])
                if allowed_senders:
                    sender = message.sender_id
                    if sender is None or sender not in allowed_senders:
                        continue

            raw_text = (message.raw_text or "").lower()
            if plan_name in ["gold", "platinum"]:
                whitelist = settings.get("whitelist", [])
                blacklist = settings.get("blacklist", [])

                if whitelist and not any(w.lower() in raw_text for w in whitelist):
                    continue
                if blacklist and any(b.lower() in raw_text for b in blacklist):
                    continue

            # --- FORWARDING ACTIONS ---
            destinations = json.loads(task["destinations"] or "[]")
            if not destinations:
                continue

            # --- WATERMARK (Platinum) ---
            watermark_text = ""
            media_file = message.media
            if plan_name == "platinum" and settings.get("watermark", False):
                watermark_text = settings.get("watermark_text", "Forwarded via DealsKoti")
                if message.media and isinstance(message.media, MessageMediaPhoto):
                    watermarked = await self._apply_image_watermark(
                        client, message, watermark_text, max_image_bytes=10 * 1024 * 1024
                    )
                    if watermarked:
                        media_file = watermarked  

            sent_tracking = []

            for dest in destinations:
                dest_id = dest.get("id")
                if not dest_id: continue

                if not await self.db.try_reserve_usage(user_id, plan.daily_messages or 0):
                    break

                sent_msg = None
                try:
                    async with self._send_semaphore:
                        if plan_name == "free":
                            sent_msg = await client.forward_messages(dest_id, message)
                        else:
                            base_text = message.message or ""
                            if plan_name == "platinum" and watermark_text and not message.media:
                                base_text = self._apply_text_watermark(base_text, watermark_text)
                                
                            new_text = self._clean_text(base_text, settings, plan_name)
                            
                            # NEW: Respect user's URL Preview setting
                            use_preview = settings.get("url_preview", bool(message.web_preview))
                            
                            sent_msg = await client.send_message(
                                dest_id,
                                message=new_text,
                                file=media_file,
                                link_preview=use_preview,
                            )
                except Exception as e:
                    logger.warning(f"Task {task['id']} failed to send to {dest_id} for user {user_id}: {e}")
                    continue

                if sent_msg:
                    sent_tracking.append((dest_id, sent_msg.id))
                    # Auto-Delete (Platinum)
                    auto_delete_secs = settings.get("auto_delete_seconds", 0)
                    if plan_name == "platinum" and auto_delete_secs > 0:
                        asyncio.create_task(self._auto_delete(client, dest_id, sent_msg.id, auto_delete_secs))

            if plan_name == "platinum" and settings.get("edit_sync", False) and sent_tracking:
                map_key = f"{user_id}:{chat_id}:{message.id}"
                self.edit_map[map_key] = {
                    "task_id": task["id"],
                    "dests": sent_tracking,
                }
                if len(self.edit_map) > 5000:
                    for k in list(self.edit_map.keys())[:2500]:
                        self.edit_map.pop(k, None)

    async def _on_message_edited(self, event: events.MessageEdited.Event, user_id: int) -> None:
        """Triggered when a source message is edited, applies live Edit Sync."""
        message: Message = event.message
        chat_id = event.chat_id

        map_key = f"{user_id}:{chat_id}:{message.id}"
        entry = self.edit_map.get(map_key)

        if not entry:
            return

        user = await self.db.get_user(user_id)
        if not user or user["plan"] != "platinum":
            return

        client = self.clients.get(user_id)
        if not client:
            return

        task = await self.db.get_task(entry["task_id"])
        if not task or int(task["user_id"]) != user_id:
            return

        settings = json.loads(task["settings"] or "{}")
        if not settings.get("edit_sync", False):
            return

        base_text = message.text or message.message or ""
        if settings.get("watermark", False):
            watermark_text = settings.get("watermark_text", "Forwarded via DealsKoti")
            base_text = self._apply_text_watermark(base_text, watermark_text)
        new_text = self._clean_text(base_text, settings, "platinum")

        # Edit preview logic
        use_preview = settings.get("url_preview", bool(message.web_preview))

        for dest_id, dest_msg_id in entry["dests"]:
            try:
                await client.edit_message(dest_id, dest_msg_id, text=new_text, link_preview=use_preview)
            except Exception as e:
                logger.debug(f"Failed to edit synced message {dest_msg_id} in {dest_id}: {e}")

    async def _auto_delete(self, client: TelegramClient, chat_id: int, message_id: int, delay_seconds: int) -> None:
        """Background task to delete a message after X seconds."""
        await asyncio.sleep(delay_seconds)
        if client.is_connected():
            with suppress(Exception):
                await client.delete_messages(chat_id, message_id)

    # --- WATERMARK ENGINE (Platinum only) ---

    def _apply_text_watermark(self, text: str, watermark_text: str) -> str:
        if not text:
            return watermark_text
        return f"{text}\n\n— {watermark_text}"

    async def _apply_image_watermark(
        self,
        client: TelegramClient,
        message: Message,
        watermark_text: str,
        max_image_bytes: int,
    ) -> bytes | None:
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow not available; skipping image watermark")
            return None

        try:
            photo_bytes = await client.download_media(
                message.media,
                file=bytes,
                thumb=0,
            )
        except Exception as e:
            logger.debug(f"Could not download source photo for watermark: {e}")
            return None

        if not photo_bytes or len(photo_bytes) > max_image_bytes:
            return None

        try:
            img = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
        except Exception as e:
            logger.debug(f"Could not decode source image: {e}")
            return None

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = max(14, min(48, img.size[1] // 20))
        font = None
        for font_path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "C:\\Windows\\Fonts\\arialbd.ttf",
            "C:\\Windows\\Fonts\\arial.ttf",
        ):
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        padding = 8
        margin = 12
        pill_w = text_w + padding * 2
        pill_h = text_h + padding * 2
        pill_x = img.size[0] - pill_w - margin
        pill_y = img.size[1] - pill_h - margin

        draw.rounded_rectangle(
            [(pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h)],
            radius=8,
            fill=(0, 0, 0, 140),
        )
        draw.text(
            (pill_x + padding, pill_y + padding - bbox[1]),
            watermark_text,
            font=font,
            fill=(255, 255, 255, 255),
        )

        out = Image.alpha_composite(img, overlay).convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
