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
from telethon.tl.types import (
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    Message,
    MessageMediaPhoto,
    MessageMediaWebPage,
    PeerChannel,
    PeerChat,
    PeerUser,
)

from .db import Database
from .plans import PLANS
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")

# Used by Replace Usernames to find @tokens in text
USERNAME_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}")

# Delay Timer waits between each destination; Anti-Ban Speed waits between
# messages. Both are advertised from Silver upward.
DELAY_PRESETS = {"off": 0.0, "fast": 1.0, "normal": 3.0, "slow": 8.0}
ANTIBAN_PRESETS = {"off": 0.0, "fast": 1.0, "normal": 3.0, "slow": 8.0}


def _preset_seconds(table: dict[str, float], value, default: str = "off") -> float:
    """Reads a speed setting that may be a preset name or a raw number."""
    if value is None:
        value = default
    if isinstance(value, (int, float)):
        return max(0.0, min(300.0, float(value)))
    key = str(value).strip().lower()
    return table.get(key, table.get(default, 0.0))


def raw_peer_id(value) -> int | None:
    """Normalises any Telegram chat id to its bare positive form.

    Telethon events expose *marked* ids (-100xxxxxxxxxx for channels, -xxxx for
    basic groups) while the ids we persist in `sources`/`destinations` come from
    `entity.id`, which is bare and positive. Bot API forwards give the marked
    form too. Comparing the two directly never matches, which is why forwarding
    silently did nothing. Everything is reduced to the bare id before comparing.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    text = str(number)
    if text.startswith("-100"):
        stripped = text[4:]
        return int(stripped) if stripped.isdigit() else None
    return abs(number)

class ForwardingEngine:
    def __init__(
        self,
        db: Database,
        telethon: TelethonService,
        max_concurrent_tasks: int = 100,
        bot_token: str = "",
        storage_channel_id: int | None = None,
    ):
        self.db = db
        self.telethon = telethon
        self.max_concurrent = max_concurrent_tasks
        self.bot_token = bot_token
        self.storage_channel_id = storage_channel_id
        
        # In-memory stores
        self.clients: dict[int, TelegramClient] = {}  # user_id -> TelegramClient
        self._running = False
        self._message_semaphore = asyncio.Semaphore(max(1, max_concurrent_tasks))
        # "user_id:raw_source_chat:msg_id" -> {"task_id": int, "dests": [(dest_id, msg_id)]}
        self.edit_map: dict[str, dict] = {}
        # Messages this engine just produced, so we never re-forward our own output
        # (which would loop forever when a destination is also somebody's source).
        self._recent_sends: set[tuple[int, int]] = set()
        self._recent_sends_order: list[tuple[int, int]] = []
        # user_id -> {raw_id: resolved telethon entity}
        self._peer_cache: dict[int, dict[int, object]] = {}
        # Users whose dialog list we already pulled once to warm the entity cache
        self._dialogs_synced: set[int] = set()
        # user_id -> (stored_file_id, monotonic_check_time, exists)
        self._stored_file_checks: dict[int, tuple[int, float, bool]] = {}

    def _remember_send(self, chat_id: int, message_id: int) -> None:
        raw = raw_peer_id(chat_id)
        if raw is None:
            return
        key = (raw, int(message_id))
        if key in self._recent_sends:
            return
        self._recent_sends.add(key)
        self._recent_sends_order.append(key)
        if len(self._recent_sends_order) > 5000:
            for stale in self._recent_sends_order[:2500]:
                self._recent_sends.discard(stale)
            del self._recent_sends_order[:2500]

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
            # Telethon has no remove_event_handlers(); detach each registered handler
            for handler, event in client.list_event_handlers():
                with suppress(Exception):
                    client.remove_event_handler(handler, event)
            if client.is_connected():
                await client.disconnect()
        # Also drop any edit-map entries belonging to this user so a future
        # reconnect doesn't accidentally replay edits on stale destinations.
        prefix = f"{user_id}:"
        for k in list(self.edit_map.keys()):
            if k.startswith(prefix):
                self.edit_map.pop(k, None)
        self._peer_cache.pop(user_id, None)
        self._dialogs_synced.discard(user_id)

    async def refresh_task(self, task_id: int) -> None:
        """Hot-reloads a user's client if a specific task was updated."""
        task = await self.db.get_task(task_id)
        if task:
            await self.refresh_user(int(task["user_id"]))

    async def remove_task(self, task_id: int) -> None:
        """Handled gracefully by refresh_user/refresh_task dynamically checking DB."""
        pass

    # --- PEER RESOLUTION ---

    async def _resolve_peer(self, client: TelegramClient, user_id: int, ref: dict):
        """Turns a stored {id, access_hash, type, username} record into something
        Telethon can send to. Bare ids are not directly usable, so we rebuild the
        proper Peer* wrapper and warm the session's entity cache from the dialog
        list the first time a lookup fails."""
        raw = raw_peer_id(ref.get("id"))
        if raw is None:
            return None

        cached = self._peer_cache.setdefault(user_id, {}).get(raw)
        if cached is not None:
            return cached

        username = (ref.get("username") or "").strip().lstrip("@")
        kind = str(ref.get("type") or "")
        access_hash = ref.get("access_hash")
        if kind in ("Channel", "ChannelForbidden") and access_hash is not None:
            try:
                peer = InputPeerChannel(channel_id=raw, access_hash=int(access_hash))
            except (TypeError, ValueError):
                peer = PeerChannel(raw)
        elif kind in ("Chat", "ChatForbidden"):
            peer = InputPeerChat(chat_id=raw)
        elif kind == "User" and access_hash is not None:
            try:
                peer = InputPeerUser(user_id=raw, access_hash=int(access_hash))
            except (TypeError, ValueError):
                peer = PeerUser(raw)
        elif kind == "User":
            peer = PeerUser(raw)
        else:
            # Unknown type: negative stored ids were already marked, so reuse them.
            peer = int(ref["id"]) if str(ref.get("id", "")).lstrip("-").isdigit() else raw

        candidates: list[object] = []
        if username:
            candidates.append(username)
        candidates.append(peer)

        for attempt in range(2):
            for candidate in candidates:
                try:
                    entity = await client.get_input_entity(candidate)
                except Exception:
                    continue
                self._peer_cache[user_id][raw] = entity
                return entity
            if attempt == 0 and user_id not in self._dialogs_synced:
                # A fresh StringSession has an empty entity cache; one dialog
                # sweep populates the access hashes we need.
                self._dialogs_synced.add(user_id)
                with suppress(Exception):
                    await client.get_dialogs(limit=200)
                continue
            break

        logger.warning("Could not resolve peer %s for user %s", ref.get("id"), user_id)
        return None

    # --- MESSAGE PROCESSING ENGINE ---

    def _clean_text(self, text: str, settings: dict, plan_name: str) -> str:
        """Applies replacements and Header/Footer based on user plan."""
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

    async def _on_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
        async with self._message_semaphore:
            await self._process_new_message(event, user_id)

    async def _process_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
        """Triggered when the user's account receives a new message in any chat."""
        message: Message = event.message
        source_raw = raw_peer_id(event.chat_id)
        if source_raw is None:
            return
        # A Message received from an event may only contain a bare peer ID.
        # Native forwarding needs the source entity/access hash explicitly;
        # otherwise Telethon tries to resolve the source again and can fail
        # with "Could not find the input entity for PeerUser(...)".
        source_entity = None
        with suppress(Exception):
            source_entity = await event.get_chat()

        # Never re-forward something this engine itself just delivered, otherwise
        # A -> B and B -> A task pairs ping-pong forever.
        if (source_raw, int(message.id)) in self._recent_sends:
            return

        client = self.clients.get(user_id)
        if not client:
            return

        user = await self.db.get_user(user_id)
        if not user or user["is_blocked"]:
            return

        tasks = await self.db.list_tasks(user_id)
        if not tasks:
            return

        plan_name = str(user["plan"] or "free")
        plan = PLANS.get(plan_name, PLANS["free"])

        # One usage read per incoming message instead of one per task.
        usage = await self.db.daily_usage(user_id)

        for task in tasks:
            if task["is_paused"]:
                continue

            # Check if this chat is a source for this task (ids normalised both ways)
            try:
                sources = json.loads(task["sources"] or "[]")
            except (TypeError, ValueError):
                continue
            source_ids = {
                raw_peer_id(s.get("id"))
                for s in sources
                if isinstance(s, dict)
            }
            if source_raw not in source_ids:
                continue

            try:
                settings = json.loads(task["settings"] or "{}")
            except (TypeError, ValueError):
                settings = {}
            if not isinstance(settings, dict):
                settings = {}

            # --- FILTERS ---

            # Sender Filter (Platinum) — accepts user IDs and @usernames
            if plan_name == "platinum":
                allowed_senders = settings.get("user_filter") or []
                if isinstance(allowed_senders, str):
                    allowed_senders = [allowed_senders]
                if allowed_senders:
                    sender = None
                    with suppress(Exception):
                        sender = await message.get_sender()
                    sid = getattr(sender, "id", None)
                    uname = (getattr(sender, "username", None) or "").lower()
                    allowed = False
                    if sid is not None and any(str(a).strip() == str(sid) for a in allowed_senders):
                        allowed = True
                    elif uname and any(str(a).lstrip("@").lower() == uname for a in allowed_senders):
                        allowed = True
                    if not allowed:
                        continue

            # Blacklist & Whitelist (Gold, Platinum)
            raw_text = (message.raw_text or "").lower()
            if plan_name in ["gold", "platinum"]:
                whitelist = settings.get("whitelist") or []
                blacklist = settings.get("blacklist") or []
                if isinstance(whitelist, str): whitelist = [whitelist]
                if isinstance(blacklist, str): blacklist = [blacklist]

                # Entries can be non-strings if they came from older task data.
                whitelist = [str(w).lower() for w in whitelist if str(w).strip()]
                blacklist = [str(b).lower() for b in blacklist if str(b).strip()]

                if whitelist and not any(w in raw_text for w in whitelist):
                    continue
                if blacklist and any(b in raw_text for b in blacklist):
                    continue

            # --- LIMITS ---
            if plan.daily_messages and usage >= plan.daily_messages:
                # Quota exceeded, ignore silently to prevent spam
                continue

            # --- FORWARDING ACTIONS ---
            try:
                destinations = json.loads(task["destinations"] or "[]")
            except (TypeError, ValueError):
                continue
            destinations = [d for d in destinations if isinstance(d, dict)]
            if not destinations:
                continue

            # --- PLATINUM STORED FILE (auto-attached to every message) ---
            stored_file = None
            # Default ON (matches main.py's toggle default) so existing platinum
            # tasks keep working until the admin/user explicitly turns it off.
            if plan_name == "platinum" and settings.get("attach_stored_file", True):
                with suppress(Exception):
                    stored_file = await self.db.get_stored_file(user_id)
                if stored_file is not None:
                    local_path = stored_file["local_path"]
                    if local_path and os.path.exists(str(local_path)):
                        # Validate periodically, not for every message. This
                        # keeps forwarding fast while making a deleted
                        # storage-channel file stop attaching shortly after.
                        check = self._stored_file_checks.get(user_id)
                        now_mono = asyncio.get_running_loop().time()
                        if (
                            check is None
                            or check[0] != int(stored_file["id"])
                            or now_mono - check[1] >= 60
                        ):
                            exists = False
                            if self.bot_token and self.storage_channel_id and stored_file["channel_message_id"]:
                                exists = await self.telethon.media_exists_big(
                                    self.bot_token,
                                    self.storage_channel_id,
                                    int(stored_file["channel_message_id"]),
                                )
                            self._stored_file_checks[user_id] = (
                                int(stored_file["id"]), now_mono, exists
                            )
                            if not exists:
                                with suppress(Exception):
                                    os.remove(str(local_path))
                                await self.db.update_stored_file_path(user_id, None)
                                stored_file = None
                    if stored_file is not None and not (
                        stored_file["local_path"] and os.path.exists(str(stored_file["local_path"]))
                    ):
                        # Railway's local filesystem is ephemeral. Restore only
                        # when the local cache is missing.
                        channel_msg_id = stored_file["channel_message_id"]
                        if self.bot_token and self.storage_channel_id and channel_msg_id:
                            restored_path = os.path.join(
                                "uploads",
                                f"stored_{user_id}_{stored_file['id']}_"
                                f"{os.path.basename(str(stored_file['file_name'] or 'file.bin')).replace(chr(0), '_')}",
                            )
                            os.makedirs("uploads", exist_ok=True)
                            restored = await self.telethon.download_media_big(
                                self.bot_token,
                                self.storage_channel_id,
                                int(channel_msg_id),
                                restored_path,
                            )
                            if restored:
                                await self.db.update_stored_file_path(user_id, restored_path)
                                stored_file = dict(stored_file)
                                stored_file["local_path"] = restored_path
                                self._stored_file_checks[user_id] = (
                                    int(stored_file["id"]),
                                    asyncio.get_running_loop().time(),
                                    True,
                                )
                            else:
                                stored_file = None
                        else:
                            stored_file = None

            # --- WATERMARK (Platinum) — build media file if enabled ---
            watermark_text = ""
            media_file = message.media
            if isinstance(media_file, MessageMediaWebPage):
                # Link previews are not sendable media; the URL lives in the text.
                media_file = None
            if plan_name == "platinum" and settings.get("watermark", False):
                watermark_text = settings.get("watermark_text") or "Forwarded via DealsKoti"
                if isinstance(message.media, MessageMediaPhoto):
                    watermarked = await self._apply_image_watermark(
                        client, message, watermark_text, max_image_bytes=10 * 1024 * 1024
                    )
                    if watermarked:
                        buffer = io.BytesIO(watermarked)
                        buffer.name = "photo.png"  # Telethon needs a name to infer type
                        media_file = buffer

            sent_tracking = []

            # Speed controls (Silver and above); free plan always runs at "off".
            paid = plan_name in ("silver", "gold", "platinum")
            dest_delay = _preset_seconds(DELAY_PRESETS, settings.get("delay_timer")) if paid else 0.0
            antiban_delay = _preset_seconds(ANTIBAN_PRESETS, settings.get("antiban_speed")) if paid else 0.0

            for index, dest in enumerate(destinations):
                if index and dest_delay:
                    await asyncio.sleep(dest_delay)
                if dest.get("id") is None:
                    continue
                dest_raw = raw_peer_id(dest.get("id"))
                if dest_raw is None or dest_raw == source_raw:
                    # Never send a chat's messages back into itself.
                    continue
                dest_peer = await self._resolve_peer(client, user_id, dest)
                if dest_peer is None:
                    continue

                sent_msg = None
                try:
                    # FREE PLAN: Native Forward (Keeps 'Forwarded from' tag)
                    if plan_name == "free":
                        forward_kwargs = {}
                        if source_entity is not None:
                            forward_kwargs["from_peer"] = source_entity
                        sent_msg = await client.forward_messages(dest_peer, message, **forward_kwargs)
                    else:
                        # PREMIUM PLANS: Clean Copy (No tag, allows formatting)
                        base_text = message.message or ""
                        # NOTE: text watermarking was removed here — it used to fire
                        # whenever media_file was None, which also covers plain
                        # text-only messages (no image at all). Watermark should
                        # only ever appear ON an image, never appended to text.
                        new_text = self._clean_text(base_text, settings, plan_name)
                        if not new_text and media_file is None:
                            # Nothing to send (e.g. service message) — skip quietly.
                            continue
                        if isinstance(media_file, io.BytesIO):
                            media_file.seek(0)
                        sent_msg = await client.send_message(
                            dest_peer,
                            message=new_text,
                            file=media_file,
                            link_preview=bool(message.web_preview),
                        )
                except Exception as e:
                    logger.warning(f"Task {task['id']} failed to send to {dest_raw} for user {user_id}: {e}")
                    continue

                if isinstance(sent_msg, list):
                    sent_msg = sent_msg[0] if sent_msg else None
                if sent_msg:
                    sent_tracking.append((dest_raw, sent_msg.id))
                    self._remember_send(dest_raw, sent_msg.id)
                    # Only count usage on SUCCESS — failed sends do not consume quota
                    await self.db.increment_usage(user_id)
                    usage += 1

                    # Platinum stored file: auto-attached to every forwarded message
                    if stored_file is not None:
                        with suppress(Exception):
                            extra = await client.send_file(dest_peer, str(stored_file["local_path"]))
                            if extra is not None:
                                extra_msg = extra[0] if isinstance(extra, list) else extra
                                self._remember_send(dest_raw, extra_msg.id)

                    # Auto-Delete (Platinum)
                    try:
                        auto_delete_secs = int(settings.get("auto_delete_seconds") or 0)
                    except (TypeError, ValueError):
                        auto_delete_secs = 0
                    if plan_name == "platinum" and auto_delete_secs > 0:
                        asyncio.create_task(self._auto_delete(client, dest_peer, sent_msg.id, auto_delete_secs))

                    if plan.daily_messages and usage >= plan.daily_messages:
                        break

            # Save to Edit Sync Map — AUTOMATIC for Platinum (no toggle needed)
            if plan_name == "platinum" and sent_tracking:
                map_key = f"{user_id}:{source_raw}:{message.id}"
                self.edit_map[map_key] = {
                    "task_id": task["id"],
                    "dests": sent_tracking,
                }
                # Cap the map size to avoid memory leaks on long-running bots
                if len(self.edit_map) > 5000:
                    # Drop oldest half (dict preserves insertion order in CPython 3.7+)
                    for k in list(self.edit_map.keys())[:2500]:
                        self.edit_map.pop(k, None)

            if sent_tracking and antiban_delay:
                # Anti-Ban Speed: pause before this account sends anything again.
                await asyncio.sleep(antiban_delay)

    async def _on_message_edited(self, event: events.MessageEdited.Event, user_id: int) -> None:
        """Triggered when a source message is edited, applies live Edit Sync."""
        message: Message = event.message
        source_raw = raw_peer_id(event.chat_id)
        if source_raw is None:
            return

        map_key = f"{user_id}:{source_raw}:{message.id}"
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

        try:
            settings = json.loads(task["settings"] or "{}")
        except (TypeError, ValueError):
            settings = {}
        if not isinstance(settings, dict):
            settings = {}

        # Destination records are needed again to rebuild sendable peers.
        try:
            dest_refs = json.loads(task["destinations"] or "[]")
        except (TypeError, ValueError):
            dest_refs = []
        by_raw = {
            raw_peer_id(d.get("id")): d
            for d in dest_refs
            if isinstance(d, dict)
        }

        base_text = message.text or message.message or ""
        # Text watermarking removed (see _on_new_message) — watermark only ever
        # applies to images, never appended to plain text.
        new_text = self._clean_text(base_text, settings, "platinum")
        if not new_text:
            return

        for dest_raw, dest_msg_id in entry["dests"]:
            ref = by_raw.get(dest_raw)
            if ref is None:
                continue
            dest_peer = await self._resolve_peer(client, user_id, ref)
            if dest_peer is None:
                continue
            try:
                await client.edit_message(dest_peer, dest_msg_id, text=new_text)
            except Exception as e:
                logger.debug(f"Failed to edit synced message {dest_msg_id} in {dest_raw}: {e}")

    async def _auto_delete(self, client: TelegramClient, chat_id: int, message_id: int, delay_seconds: int) -> None:
        """Background task to delete a message after X seconds."""
        await asyncio.sleep(delay_seconds)
        if client.is_connected():
            with suppress(Exception):
                await client.delete_messages(chat_id, message_id)

    # --- WATERMARK ENGINE (Platinum only) ---
    # NOTE: text watermarking (_apply_text_watermark) was intentionally removed —
    # watermark is now an IMAGE-only feature, never appended to text messages.

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

        # Download the full-size photo. Passing thumb=0 would fetch the *smallest*
        # thumbnail, producing a blurry watermarked image.
        try:
            photo_bytes = await client.download_media(message.media, file=bytes)
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

        # Choose font size proportional to image height (capped).
        # Was max(14, min(48, h//20)) — too small on typical phone-camera photos.
        # Bigger floor/cap + a larger divisor makes it clearly legible.
        font_size = max(22, min(72, img.size[1] // 14))
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
