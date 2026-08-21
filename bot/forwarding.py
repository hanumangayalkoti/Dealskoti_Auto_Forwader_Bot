import asyncio
import io
import json
import logging
import os
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

# Speed presets (seconds between sends). Keys mirror the UI buttons.
DELAY_PRESETS: dict[str, int] = {"off": 0, "slow": 20, "normal": 10, "fast": 5}
ANTIBAN_PRESETS: dict[str, int] = {"slow": 12, "normal": 6, "fast": 2}

USERNAME_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}")
LINK_RE = re.compile(r"(?:https?://\S+|www\.\S+|(?<![\w@.])t\.me/\S+)", re.IGNORECASE)
# Hidden/background invite links commonly spammed inside posts
BG_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)\S+|tg://resolve\?\S+", re.IGNORECASE)

class ForwardingEngine:
    def __init__(self, db: Database, telethon: TelethonService, max_concurrent_tasks: int = 100):
        self.db = db
        self.telethon = telethon
        self.max_concurrent = max_concurrent_tasks
        
        # In-memory stores
        self.clients: dict[int, TelegramClient] = {}  # user_id -> TelegramClient
        self._running = False
        self.edit_map: dict[str, list[tuple[int, int]]] = {}  # "user_id:source_chat:msg_id" -> [(dest_chat, dest_msg_id)]
        self.last_sent: dict[tuple[int, int], int] = {}  # (user_id, dest_id) -> last sent msg id (reply sync)

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
                    # refresh_user refused to register the client, so the stored
                    # session must be invalid — count it so we log a useful number.
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
        # Also drop any edit-map entries belonging to this user so a future
        # reconnect doesn't accidentally replay edits on stale destinations.
        prefix = f"{user_id}:"
        for k in list(self.edit_map.keys()):
            if k.startswith(prefix):
                self.edit_map.pop(k, None)
        for key in list(self.last_sent.keys()):
            if key[0] == user_id:
                self.last_sent.pop(key, None)

    async def refresh_task(self, task_id: int) -> None:
        """Hot-reloads a user's client if a specific task was updated."""
        task = await self.db.get_task(task_id)
        if task:
            await self.refresh_user(int(task["user_id"]))

    async def remove_task(self, task_id: int) -> None:
        """Handled gracefully by refresh_user/refresh_task dynamically checking DB."""
        pass

    # --- MESSAGE PROCESSING ENGINE ---

    def _clean_text(self, text: str, settings: dict, plan_name: str) -> str:
        """Applies removals, replacements, and Header/Footer based on user plan."""
        if not text:
            # Even with empty body, header/footer should still be appended for paid plans.
            if plan_name in ["silver", "gold", "platinum"]:
                header = settings.get("header", "")
                footer = settings.get("footer", "")
                if header or footer:
                    parts = []
                    if header: parts.append(header)
                    if footer: parts.append(footer)
                    return "\n\n".join(parts)
            return text

        # --- REMOVALS (Silver+) ---
        if plan_name in ["silver", "gold", "platinum"]:
            if settings.get("remove_usernames", False):
                text = USERNAME_RE.sub("", text)
            if settings.get("remove_links", False):
                text = LINK_RE.sub("", text)

        # --- HIDDEN/BACKGROUND LINKS (Gold+) ---
        if plan_name in ["gold", "platinum"] and settings.get("disable_bg_links", False):
            text = BG_LINK_RE.sub("", text)

        # --- REPLACEMENTS ---
        # Legacy plain replace (Gold+) — kept for backward compatibility with old tasks
        if plan_name in ["gold", "platinum"]:
            replacements = settings.get("replace", {})
            if isinstance(replacements, dict):
                for old_word, new_word in replacements.items():
                    if old_word:
                        text = text.replace(old_word, new_word)

        if plan_name == "platinum":
            replace_words = settings.get("replace_words", {})
            if isinstance(replace_words, dict):
                for old_word, new_word in replace_words.items():
                    if old_word:
                        text = text.replace(old_word, new_word)

            replace_usernames = settings.get("replace_usernames", {})
            if isinstance(replace_usernames, dict) and replace_usernames:
                def _sub_username(match: re.Match) -> str:
                    token = match.group(0)
                    return replace_usernames.get(token, replace_usernames.get(token[1:], token))
                text = USERNAME_RE.sub(_sub_username, text)

            replace_links = settings.get("replace_links", {})
            if isinstance(replace_links, dict) and replace_links:
                for old_link, new_link in replace_links.items():
                    if old_link and old_link in text:
                        text = text.replace(old_link, new_link)

        # --- HEADER / FOOTER (Silver+) ---
        if plan_name in ["silver", "gold", "platinum"]:
            header = settings.get("header", "")
            footer = settings.get("footer", "")

            parts = []
            if header:
                parts.append(header)
            parts.append(text)
            if footer:
                parts.append(footer)

            text = "\n\n".join(parts)

        return text

    @staticmethod
    def _message_ext(message: Message) -> str:
        """Extension of the message's document file, lowercase without dot ('' if none)."""
        doc = getattr(message, "document", None)
        name = getattr(doc, "name", None) if doc is not None else None
        if not name:
            mime = getattr(doc, "mime_type", "") if doc is not None else ""
            if "/" in mime:
                return mime.split("/")[-1].split("+")[0]
            return ""
        return os.path.splitext(name)[1].lower().lstrip(".")

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

            # Check if this chat is a source for this task
            sources = json.loads(task["sources"] or "[]")
            source_ids = [s.get("id") for s in sources if isinstance(s, dict)]
            if chat_id not in source_ids:
                continue

            settings = json.loads(task["settings"] or "{}")

            # --- FILTERS ---

            # Sender Filter (Platinum) — apply safely; None sender cannot match
            if plan_name == "platinum":
                allowed_senders = settings.get("user_filter", [])
                if allowed_senders:
                    sender = message.sender_id
                    if sender is None or sender not in allowed_senders:
                        continue

            # Blacklist & Whitelist (Gold, Platinum)
            raw_text = (message.raw_text or "").lower()
            if plan_name in ["gold", "platinum"]:
                whitelist = settings.get("whitelist", [])
                blacklist = settings.get("blacklist", [])

                if whitelist and not any(w.lower() in raw_text for w in whitelist):
                    continue
                if blacklist and any(b.lower() in raw_text for b in blacklist):
                    continue

            # --- LIMITS ---
            usage = await self.db.daily_usage(user_id)
            if plan.daily_messages and usage >= plan.daily_messages:
                # Quota exceeded, ignore silently to prevent spam
                continue

            # --- MEDIA FORWARD TOGGLE (Silver+) ---
            if plan_name != "free" and not settings.get("media_forward", True) and message.media:
                continue

            # --- FORWARDING ACTIONS ---
            destinations = json.loads(task["destinations"] or "[]")
            if not destinations:
                continue

            # --- PLATINUM STORED FILE (attach / replace-same-type) ---
            stored_file = None
            if plan_name == "platinum" and (settings.get("attach_file_every") or settings.get("replace_same_ext")):
                with suppress(Exception):
                    stored_file = await self.db.get_stored_file(user_id)
                if stored_file is not None and not (stored_file["local_path"] or stored_file["channel_message_id"]):
                    stored_file = None
            replace_media = bool(
                stored_file is not None
                and settings.get("replace_same_ext")
                and stored_file["extension"]
                and str(stored_file["extension"]).lower() == self._message_ext(message)
            )

            # --- SPEED PRESETS (Delay Timer + Anti-Ban) ---
            try:
                delay_secs = int(settings.get("delay_timer_seconds", 0) or 0)
            except (TypeError, ValueError):
                delay_secs = 0
            try:
                antiban_secs = int(settings.get("anti_ban_speed_seconds", 0) or 0)
            except (TypeError, ValueError):
                antiban_secs = 0
            send_gap = max(delay_secs, antiban_secs)

            # --- WATERMARK (Platinum) — build media file if enabled ---
            watermark_text = ""
            media_file = message.media
            if replace_media:
                media_file = None  # original media replaced by the user's uploaded file
            if plan_name == "platinum" and settings.get("watermark", False):
                watermark_text = settings.get("watermark_text", "Forwarded via DealsKoti")
                if message.media and isinstance(message.media, MessageMediaPhoto):
                    watermarked = await self._apply_image_watermark(
                        client, message, watermark_text, max_image_bytes=10 * 1024 * 1024
                    )
                    if watermarked:
                        media_file = watermarked  # bytes are accepted by send_message(file=...)

            sent_tracking = []
            sent_count = 0

            for dest in destinations:
                dest_id = dest.get("id")
                if not dest_id: continue

                # Speed gap between sends (never before the first send)
                if send_gap > 0 and sent_count > 0:
                    await asyncio.sleep(send_gap)

                # Reply Sync (Silver+): thread replies onto our last sent message
                reply_to_id = None
                if plan_name != "free" and settings.get("reply_sync", False) and message.reply_to_msg_id:
                    reply_to_id = self.last_sent.get((user_id, dest_id))

                sent_msg = None
                try:
                    # FREE PLAN: Native Forward (Keeps 'Forwarded from' tag)
                    if plan_name == "free":
                        sent_msg = await client.forward_messages(dest_id, message)
                    else:
                        # PREMIUM PLANS: Clean Copy (No tag, allows formatting)
                        base_text = message.message or ""
                        if plan_name == "platinum" and watermark_text and not message.media:
                            # text watermark only when there's no image watermark to draw
                            base_text = self._apply_text_watermark(base_text, watermark_text)
                        new_text = self._clean_text(base_text, settings, plan_name)
                        link_preview = bool(message.web_preview) and not settings.get("disable_bg_links", False)
                        sent_msg = await client.send_message(
                            dest_id,
                            message=new_text,
                            file=media_file,
                            link_preview=link_preview,
                            reply_to=reply_to_id,
                        )
                except Exception as e:
                    logger.warning(f"Task {task['id']} failed to send to {dest_id} for user {user_id}: {e}")
                    continue

                if sent_msg:
                    sent_tracking.append((dest_id, sent_msg.id))
                    sent_count += 1
                    self.last_sent[(user_id, dest_id)] = sent_msg.id
                    # Only count usage on SUCCESS — failed sends do not consume quota
                    await self.db.increment_usage(user_id)

                    # Platinum stored file: attach to every message / replaced media
                    if stored_file is not None and (replace_media or settings.get("attach_file_every")):
                        file_ref: object = None
                        local_path = stored_file["local_path"]
                        if local_path and os.path.exists(str(local_path)):
                            file_ref = str(local_path)
                        elif stored_file["channel_message_id"]:
                            file_ref = int(stored_file["channel_message_id"])
                        if file_ref is not None:
                            with suppress(Exception):
                                await client.send_file(dest_id, file_ref)

                    # Auto-Delete (Platinum)
                    auto_delete_secs = settings.get("auto_delete_seconds", 0)
                    if plan_name == "platinum" and auto_delete_secs > 0:
                        asyncio.create_task(self._auto_delete(client, dest_id, sent_msg.id, auto_delete_secs))

            # Save to Edit Sync Map — AUTOMATIC for Platinum (no toggle needed)
            if plan_name == "platinum" and sent_tracking:
                map_key = f"{user_id}:{chat_id}:{message.id}"
                self.edit_map[map_key] = {
                    "task_id": task["id"],
                    "dests": sent_tracking,
                }
                # Cap the map size to avoid memory leaks on long-running bots
                if len(self.edit_map) > 5000:
                    # Drop oldest half (dict preserves insertion order in CPython 3.7+)
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

        # Use the EXACT task whose settings were applied at forward-time
        task = await self.db.get_task(entry["task_id"])
        if not task or int(task["user_id"]) != user_id:
            return

        settings = json.loads(task["settings"] or "{}")

        base_text = message.text or message.message or ""
        if settings.get("watermark", False):
            watermark_text = settings.get("watermark_text", "Forwarded via DealsKoti")
            base_text = self._apply_text_watermark(base_text, watermark_text)
        new_text = self._clean_text(base_text, settings, "platinum")

        for dest_id, dest_msg_id in entry["dests"]:
            try:
                await client.edit_message(dest_id, dest_msg_id, text=new_text)
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
        """Append a subtle watermark line to text content. Platinum-only feature."""
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
        """Download a photo, draw watermark on bottom-right, return PNG bytes.
        Returns None if the message has no downloadable photo or processing fails."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow not available; skipping image watermark")
            return None

        # Try to get the largest available photo
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

        # Create a transparent overlay for the watermark
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Choose font size proportional to image height (capped)
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

        # Measure text and place a semi-transparent black pill behind it for legibility
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        padding = 8
        margin = 12
        pill_w = text_w + padding * 2
        pill_h = text_h + padding * 2
        pill_x = img.size[0] - pill_w - margin
        pill_y = img.size[1] - pill_h - margin

        # Draw rounded background pill
        draw.rounded_rectangle(
            [(pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h)],
            radius=8,
            fill=(0, 0, 0, 140),
        )
        # Draw white text on top
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
