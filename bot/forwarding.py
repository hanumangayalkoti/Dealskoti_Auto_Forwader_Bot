"""
Real-time message forwarding engine.

One connected Telethon client per user listens for new/edited messages and
copies them to every destination of every matching task.

NON-NEGOTIABLE RULES (do not "optimise" these away in a rewrite):
  1. Feature access is ALWAYS decided by plans.plan_has(). Never write
     `if plan == "platinum"` in this file — a tier change must only ever
     require editing plans.py.
  2. Usage is incremented only AFTER a send succeeds. A failed send must
     never consume the user's daily quota.
  3. Every message this engine sends is registered with _remember_send() so
     an A->B / B->A task pair cannot ping-pong forever.
  4. Cosmetic extras (reactions, stored-file attachment, edit-sync bookkeeping)
     must never be able to break a forward that already succeeded — they are
     all wrapped in suppress()/try.
"""

import asyncio
import html as html_lib
import io
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from contextlib import suppress

from telethon import TelegramClient, errors, events, functions, types
from telethon.sessions import StringSession
from telethon.tl.types import (
    InputPeerChannel,
    InputPeerChat,
    InputPeerUser,
    Message,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageMediaPhoto,
    MessageMediaWebPage,
    PeerChannel,
    PeerUser,
)

from .db import Database
from .plans import (
    PLANS,
    F_ANTIBAN,
    F_ATTACH_FILE,
    F_AUTO_DELETE,
    F_AUTO_REACTION,
    F_BLACKLIST,
    F_DELAY_TIMER,
    F_FOOTER,
    F_HEADER,
    F_HIDDEN_LINKS,
    F_LINK_PREVIEW,
    F_MONO_TEXT,
    F_NO_WATERMARK,
    F_PER_TARGET_HF,
    F_POST_EDIT_SYNC,
    F_REMOVE_LINKS,
    F_REMOVE_USERNAMES,
    F_REPLACE_LINKS,
    F_REPLACE_USERNAMES,
    F_REPLACE_WORDS,
    F_SENDER_FILTER,
    F_TOPICS,
    F_TRIM_WORDS,
    F_WATERMARK_IMAGE,
    F_WATERMARK_STYLE,
    F_WHITELIST,
    plan_has,
)
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.forwarding")

# Finds @handles in text (Replace Usernames / Remove Usernames).
USERNAME_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}")

# Finds URLs in text (Remove Links). Covers http(s):// and bare domains
# like t.me/foo and www.example.com which Telegram also renders as links.
URL_RE = re.compile(
    r"""(?ix)
    \b(
        https?://[^\s<>"']+
        | www\.[^\s<>"']+
        | (?:t\.me|telegram\.me|telegram\.dog)/[^\s<>"']+
    )
    """
)

# Delay Timer waits between each destination; Anti-Ban Speed waits between
# messages.
DELAY_PRESETS = {"off": 0.0, "fast": 1.0, "normal": 3.0, "slow": 8.0}
ANTIBAN_PRESETS = {"off": 0.0, "fast": 1.0, "normal": 3.0, "slow": 8.0}

# Watermark style options (Platinum). Kept here so main.py's picker and the
# renderer can never disagree about what a valid value is.
WATERMARK_POSITIONS = ("bottom_right", "bottom_left", "top_right", "top_left", "center")
WATERMARK_SIZES = {"small": 22, "medium": 14, "large": 9}  # divisor of image height
WATERMARK_OPACITIES = (30, 50, 70, 100)

DEFAULT_REACTION_EMOJI = "👍"

# How many destinations to send to at once when no Delay Timer is set.
# Kept deliberately low: Telegram rate-limits per account, and a large burst
# earns a FloodWait that is far slower than sending in small batches.
# Lowered from 4 after FloodWait was suspected of causing invisible stalls.
PARALLEL_SENDS = 2


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
    `entity.id`, which is bare and positive. Comparing the two directly never
    matches, which is why forwarding once silently did nothing.
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


def message_topic_id(message: Message) -> int | None:
    """Returns the forum topic id a message belongs to, or None.

    In a forum supergroup every message carries reply_to.forum_topic=True and
    the topic's root id. Topic 1 is the built-in "General" topic.
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None
    if not getattr(reply_to, "forum_topic", False):
        return None
    return getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "reply_to_msg_id", None)


# ---- CODE FILTER ----
# Channels hide gift/coupon codes in one of two Telegram formats, and which one
# they use varies by channel, so the filter is a choice rather than a toggle.
CODE_FILTER_OFF = "off"
CODE_FILTER_MONO = "mono"
CODE_FILTER_SPOILER = "spoiler"
CODE_FILTER_BOTH = "both"
CODE_FILTER_MODES = (CODE_FILTER_OFF, CODE_FILTER_MONO, CODE_FILTER_SPOILER, CODE_FILTER_BOTH)


def code_filter_mode(settings: dict) -> str:
    """Reads the code-filter setting.

    The setting used to be a plain boolean, so existing tasks store True/False.
    True is read as "mono" to preserve exactly what those tasks already do.
    """
    raw = settings.get("mono_text")
    if raw is None or raw is False:
        return CODE_FILTER_OFF
    if raw is True:
        return CODE_FILTER_MONO
    value = str(raw).strip().lower()
    return value if value in CODE_FILTER_MODES else CODE_FILTER_OFF


def extract_code_spans(text: str, entities, mode: str) -> list[str]:
    """Returns every monospace and/or spoiler run in a message, in order.

    Monospace covers inline `code` and ```pre``` blocks; spoiler is Telegram's
    tap-to-reveal hidden text (the shimmering-particles style).

    Telegram entity offsets count UTF-16 code units rather than Python
    characters, so the text is sliced in UTF-16 space — slicing by character
    index corrupts any message containing an emoji, which deals posts are
    full of.
    """
    if not text or not entities or mode == CODE_FILTER_OFF:
        return []

    wanted: tuple = ()
    if mode in (CODE_FILTER_MONO, CODE_FILTER_BOTH):
        wanted += (MessageEntityCode, MessageEntityPre)
    if mode in (CODE_FILTER_SPOILER, CODE_FILTER_BOTH):
        wanted += (MessageEntitySpoiler,)
    if not wanted:
        return []

    buf = text.encode("utf-16-le")
    spans: list[str] = []
    for ent in entities:
        if not isinstance(ent, wanted):
            continue
        start, end = int(ent.offset) * 2, (int(ent.offset) + int(ent.length)) * 2
        if start < 0 or end > len(buf) or end <= start:
            continue
        piece = buf[start:end].decode("utf-16-le", errors="ignore").strip()
        if piece:
            spans.append(piece)
    return spans


def extract_mono_spans(text: str, entities) -> list[str]:
    """Backwards-compatible wrapper for any older call site."""
    return extract_code_spans(text, entities, CODE_FILTER_MONO)


# ==========================================
# OFFSET-AWARE TEXT EDITING
# ==========================================
# Telegram stores formatting (bold, links and CUSTOM/animated emoji) as
# entities pinned to character positions, not inside the text. A plain string
# replacement moves every character after it and leaves those positions
# pointing at the wrong place — which is why an earlier build simply threw the
# entities away whenever any replacement rule was active, silently stripping
# premium emoji from the message.
#
# _Rich edits the text and the entity positions TOGETHER, so replacements,
# removals and trims all keep the formatting exactly where it belongs.
#
# Entity offsets from Telegram count UTF-16 code units; Python indexes by code
# point. They differ for every emoji, so offsets are converted on the way in
# and back out, and all editing happens in Python index space.


def _u16_to_py(text: str, offset: int) -> int:
    """UTF-16 code-unit offset -> Python character index."""
    if offset <= 0:
        return 0
    count = 0
    for i, ch in enumerate(text):
        if count >= offset:
            return i
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def _py_to_u16(text: str, index: int) -> int:
    """Python character index -> UTF-16 code-unit offset."""
    return len(text[:index].encode("utf-16-le")) // 2


class _Rich:
    """Text plus its entities, editable without breaking either."""

    def __init__(self, text: str, entities=None):
        self.text = text or ""
        self.spans: list[list] = []
        for ent in entities or []:
            try:
                start = _u16_to_py(self.text, int(ent.offset))
                end = _u16_to_py(self.text, int(ent.offset) + int(ent.length))
            except (TypeError, ValueError):
                continue
            if end > start:
                self.spans.append([ent, start, end])

    def _apply(self, start: int, end: int, replacement: str) -> None:
        """Replaces text[start:end] and moves every entity position with it."""
        delta = len(replacement) - (end - start)
        self.text = self.text[:start] + replacement + self.text[end:]

        def move(pos: int, is_end: bool) -> int:
            if pos <= start:
                return pos
            if pos >= end:
                return pos + delta
            # Inside the replaced region: clamp to its edge. An entity wholly
            # inside is collapsed and dropped below — correct, because the text
            # it described no longer exists.
            return start + (len(replacement) if is_end else 0)

        kept = []
        for span in self.spans:
            new_start = move(span[1], False)
            new_end = move(span[2], True)
            if new_end > new_start:
                kept.append([span[0], new_start, new_end])
        self.spans = kept

    def sub(self, pattern: re.Pattern, repl) -> None:
        """Regex replace. Matches are applied right-to-left so each edit cannot
        disturb the positions of the ones still to come."""
        matches = list(pattern.finditer(self.text))
        for match in reversed(matches):
            value = repl(match) if callable(repl) else repl
            self._apply(match.start(), match.end(), value)

    def replace_literal(self, needle: str, value: str) -> None:
        if not needle:
            return
        start = 0
        found = []
        while True:
            idx = self.text.find(needle, start)
            if idx < 0:
                break
            found.append(idx)
            start = idx + len(needle)
        for idx in reversed(found):
            self._apply(idx, idx + len(needle), value)

    def entities(self):
        """Entities back in Telegram's UTF-16 coordinates, ready to send."""
        import copy as _copy

        out = []
        for ent, start, end in self.spans:
            offset = _py_to_u16(self.text, start)
            length = _py_to_u16(self.text, end) - offset
            if length <= 0:
                continue
            clone = _copy.copy(ent)
            clone.offset = offset
            clone.length = length
            out.append(clone)
        return out or None


async def _async_iter(items):
    """Wraps a plain list so both transfer paths can use `async for`."""
    for item in items:
        yield item


def _shift_entities(entities, shift: int):
    """Returns a copy of the message entities moved along by `shift` units.

    Entities carry bold/italic/links AND custom (animated) emoji. Sending a
    clean copy without them silently stripped all of that — paid users were
    getting worse fidelity than free users, whose native forward keeps
    everything.

    Copies are made so the original message object is never mutated: the same
    message is rendered once per destination.
    """
    if not entities:
        return None
    import copy as _copy

    out = []
    for ent in entities:
        # A web-page preview entity is not sendable and Telegram rejects it.
        if type(ent).__name__ == "MessageEntityUnknown":
            continue
        clone = _copy.copy(ent)
        try:
            clone.offset = int(ent.offset) + shift
        except (TypeError, ValueError):
            continue
        if clone.offset < 0:
            continue
        out.append(clone)
    return out or None


def _as_list(value) -> list:
    """Settings written by older versions may be a bare string or None."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return []


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


class ForwardingEngine:
    def __init__(
        self,
        db: Database,
        telethon: TelethonService,
        max_concurrent_tasks: int = 100,
        bot_token: str = "",
        storage_channel_id: int | None = None,
        bot=None,
    ):
        # The control Bot, used ONLY to warn users about problems they cannot
        # otherwise see (quota exhausted, a destination that keeps failing).
        # Silent failure is what makes people think the bot is broken.
        self.bot = bot
        self.db = db
        self.telethon = telethon
        self.max_concurrent = max_concurrent_tasks
        self.bot_token = bot_token
        self.storage_channel_id = storage_channel_id

        # In-memory stores
        self.clients: dict[int, TelegramClient] = {}  # user_id -> TelegramClient
        self._running = False
        # Separate lanes per tier. With one shared semaphore a burst of Free
        # traffic queued ahead of paying users and slowed them down, which is
        # exactly backwards.
        base = max(4, max_concurrent_tasks)
        self._lanes: dict[str, asyncio.Semaphore] = {
            "platinum": asyncio.Semaphore(base),
            "gold": asyncio.Semaphore(max(2, base // 2)),
            "silver": asyncio.Semaphore(max(2, base // 4)),
            "basic": asyncio.Semaphore(max(2, base // 6)),
            "free": asyncio.Semaphore(max(1, base // 8)),
        }
        self._message_semaphore = self._lanes["free"]  # kept for compatibility
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
        # (task_id, dest_raw) -> consecutive failures, so one flaky send does
        # not spam the user but a genuinely broken destination does get flagged
        self._dest_failures: dict[tuple[int, int], int] = {}
        # destinations already reported, so we warn once and not every message
        self._dest_reported: set[tuple[int, int]] = set()
        # (task_id, source_raw) -> already told the user this source is protected
        self._protected_reported: dict[tuple[int, int], bool] = {}
        # Counter surfaced in the logs so rate limiting is measurable, not guessed
        self._floodwaits = 0
        # user_id -> live bulk-transfer state (progress + cancel flag)
        self._transfers: dict[int, dict] = {}
        # user_id -> monotonic time before which this account should not send
        # again. Enforced BEFORE taking a lane slot, so a user's own anti-ban
        # delay never consumes shared capacity.
        self._cooldowns: dict[int, float] = {}

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

    # ==========================================
    # LIFECYCLE
    # ==========================================

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
        logger.info(
            f"Forwarding Engine started. Active clients: {len(self.clients)}. "
            f"Invalid sessions cleared: {bad_sessions}"
        )

    async def stop(self) -> None:
        """Stops the engine and safely disconnects all clients."""
        self._running = False
        for user_id in list(self.clients.keys()):
            await self.remove_user(user_id)
        logger.info("Forwarding Engine stopped.")

    async def run_until_stopped(self) -> None:
        while self._running:
            await asyncio.sleep(1)

    # ==========================================
    # CLIENT MANAGEMENT
    # ==========================================

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
            with suppress(Exception):
                await self.telethon.disconnect(user_id)
            return

        try:
            await client.connect()
            if not await client.is_user_authorized():
                await self.telethon.disconnect(user_id)
                if client.is_connected():
                    await client.disconnect()
                return

            # Telethon handles SHORT rate limits itself by sleeping. Setting
            # this to 0 (as an earlier build did) made every 1-2 second limit
            # raise instead, which pushed ordinary messages down the degraded
            # retry path and lost their media and formatting. 20s is the
            # balance: routine limits are absorbed quietly, anything longer
            # reaches our own handler and gets logged.
            client.flood_sleep_threshold = 20

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
        self._peer_cache.pop(user_id, None)
        self._dialogs_synced.discard(user_id)
        self._stored_file_checks.pop(user_id, None)

    async def refresh_task(self, task_id: int) -> None:
        """Hot-reloads a user's client if a specific task was updated."""
        task = await self.db.get_task(task_id)
        if task:
            await self.refresh_user(int(task["user_id"]))

    async def remove_task(self, task_id: int) -> None:
        """Handled gracefully by refresh_user/refresh_task dynamically checking DB."""
        return None

    # ==========================================
    # PEER RESOLUTION
    # ==========================================

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

    # ==========================================
    # TEXT PIPELINE
    # ==========================================

    def _reveal_hidden_links(self, text: str, entities) -> str:
        """Disable Hidden Links — turns masked hyperlinks into visible URLs.

        Telegram lets a message show friendly words while the real destination
        hides behind them. Any entity carrying a `url` is rewritten as
        "visible text (real-url)" so nothing is disguised.

        Telegram entity offsets count UTF-16 code units, not Python characters,
        so the text is sliced in UTF-16 space. Slicing by character index would
        corrupt any message containing an emoji.
        """
        if not text or not entities:
            return text

        marks = []
        for ent in entities:
            url = getattr(ent, "url", None)
            if not url:
                continue
            marks.append((int(ent.offset), int(ent.length), str(url)))
        if not marks:
            return text

        buf = text.encode("utf-16-le")
        pieces: list[str] = []
        cursor = 0
        for offset, length, url in sorted(marks, key=lambda m: m[0]):
            start, end = offset * 2, (offset + length) * 2
            if start < cursor or end > len(buf):
                continue
            pieces.append(buf[cursor:start].decode("utf-16-le", errors="ignore"))
            visible = buf[start:end].decode("utf-16-le", errors="ignore")
            pieces.append(visible if visible.strip() == url.strip() else f"{visible} ({url})")
            cursor = end
        pieces.append(buf[cursor:].decode("utf-16-le", errors="ignore"))
        return "".join(pieces)

    def _apply_replacements(self, rich: "_Rich", settings: dict, plan_name: str) -> None:
        """Replace Words / Usernames / Links — the user's explicit swaps.

        Edits go through _Rich so entity positions move with the text and
        premium emoji survive the replacement.
        """
        if plan_has(plan_name, F_REPLACE_WORDS):
            # "replace" is the legacy key; kept so old tasks keep working.
            for key in ("replace", "replace_words"):
                for old_word, new_word in _as_dict(settings.get(key)).items():
                    if old_word:
                        rich.replace_literal(str(old_word), str(new_word))

        if plan_has(plan_name, F_REPLACE_USERNAMES):
            mapping = _as_dict(settings.get("replace_usernames"))
            if mapping:
                lookup = {}
                for old, new in mapping.items():
                    lookup[str(old).lower()] = str(new)
                    lookup[str(old).lstrip("@").lower()] = str(new)

                def _sub_username(match: re.Match) -> str:
                    token = match.group(0)
                    return lookup.get(token.lower(), lookup.get(token[1:].lower(), token))

                rich.sub(USERNAME_RE, _sub_username)

        if plan_has(plan_name, F_REPLACE_LINKS):
            for old_link, new_link in _as_dict(settings.get("replace_links")).items():
                if old_link:
                    rich.replace_literal(str(old_link), str(new_link))

    def _apply_trim(self, rich: "_Rich", settings: dict, plan_name: str) -> None:
        """Trim Single Words/Lines.

        Each entry is removed wherever it appears. If removing it leaves a line
        empty, that whole line goes too — otherwise the message fills up with
        blank gaps where the trimmed words used to be.
        """
        if not plan_has(plan_name, F_TRIM_WORDS):
            return
        words = [str(w).strip() for w in _as_list(settings.get("trim_words")) if str(w).strip()]
        if not words:
            return

        for word in words:
            rich.sub(re.compile(re.escape(word), re.IGNORECASE), "")

        # Drop lines that are now empty, still keeping entity positions intact.
        while True:
            match = re.search(r"\n[ \t]*\n[ \t]*\n", rich.text)
            if not match:
                break
            rich._apply(match.start(), match.end(), "\n\n")
        rich.sub(re.compile(r"^[ \t]*\n"), "")

    def _apply_removals(self, rich: "_Rich", settings: dict, plan_name: str) -> None:
        """Remove Usernames / Remove Links — blanket strip toggles.

        Runs AFTER replacements, so a user who set up a replacement gets their
        swap applied first. Header/footer are added later and are never touched
        by this, so the user's own handles and links always survive.
        """
        touched = False
        if plan_has(plan_name, F_REMOVE_USERNAMES) and settings.get("remove_usernames"):
            rich.sub(USERNAME_RE, "")
            touched = True

        if plan_has(plan_name, F_REMOVE_LINKS) and settings.get("remove_links"):
            rich.sub(URL_RE, "")
            touched = True

        if touched:
            # Tidy up the double spaces and empty lines the strip leaves behind.
            rich.sub(re.compile(r"[ \t]{2,}"), " ")
            while True:
                match = re.search(r"\n[ \t]*\n[ \t]*\n", rich.text)
                if not match:
                    break
                rich._apply(match.start(), match.end(), "\n\n")

    def _header_footer_for(self, settings: dict, plan_name: str, dest_raw: int | None) -> tuple[str, str]:
        """Returns (header, footer) for one destination.

        Custom Header/Footer Per Target overrides the task-wide pair when the
        plan allows it and an entry exists for this destination.
        """
        header = str(settings.get("header") or "") if plan_has(plan_name, F_HEADER) else ""
        footer = str(settings.get("footer") or "") if plan_has(plan_name, F_FOOTER) else ""

        if plan_has(plan_name, F_PER_TARGET_HF) and dest_raw is not None:
            per_target = _as_dict(settings.get("per_target_hf")).get(str(dest_raw))
            if isinstance(per_target, dict):
                if per_target.get("header") is not None:
                    header = str(per_target.get("header") or "")
                if per_target.get("footer") is not None:
                    footer = str(per_target.get("footer") or "")
        return header, footer

    def code_filter_for(self, settings: dict, plan_name: str) -> str:
        """The active code-filter mode, or "off" if the plan cannot use it."""
        if not plan_has(plan_name, F_MONO_TEXT):
            return CODE_FILTER_OFF
        return code_filter_mode(settings)

    def mono_enabled(self, settings: dict, plan_name: str) -> bool:
        return self.code_filter_for(settings, plan_name) != CODE_FILTER_OFF

    def code_body(self, message: Message | None, mode: str) -> str:
        """The code-only body of a message, or "" when it has none.

        The Code Filter is a FILTER, not a formatter: when on, only the parts
        the source author marked as monospace and/or spoiler (gift codes,
        coupon codes) are forwarded and all surrounding chatter is dropped.
        Multiple runs are joined with newlines so a post carrying several
        codes keeps all of them.
        """
        if message is None or mode == CODE_FILTER_OFF:
            return ""
        spans = extract_code_spans(
            message.message or "", getattr(message, "entities", None), mode,
        )
        return "\n".join(spans)

    def mono_body(self, message: Message | None) -> str:
        """Backwards-compatible wrapper for any older call site."""
        return self.code_body(message, CODE_FILTER_MONO)

    def build_text(
        self,
        message: Message | None,
        base_text: str,
        settings: dict,
        plan_name: str,
        dest_raw: int | None = None,
    ) -> tuple[str, str | None]:
        """Runs the full text pipeline for ONE destination.

        Returns (text, parse_mode). parse_mode is "html" only when Mono Text is
        on, because that is the one case where we inject markup ourselves.

        Order matters and is deliberate:
          1. code filter           (keep ONLY the source's code/spoiler parts)
          2. reveal hidden links   (needs the original entities)
          3. replacements          (the user's explicit swaps win first)
          4. trim words/lines
          5. blanket removals
          6. header / footer       (added last so they are never stripped)

        When the Code Filter is on and the source has no matching content this
        returns "", and the caller MUST skip the message rather than sending an
        empty one. Use code_filter_for()/code_body() to check that up front.
        """
        mode = self.code_filter_for(settings, plan_name)
        mono = mode != CODE_FILTER_OFF

        # Entities (bold, italic, links and CUSTOM/animated emoji) live
        # separately from the text, keyed by character offsets. They can only
        # be reused when the body is untouched — any replacement or removal
        # shifts every offset after it and would smear the formatting onto the
        # wrong words. A header only shifts everything by a fixed amount, which
        # IS computable, so that case is still preserved.
        mode = self.code_filter_for(settings, plan_name)
        mono = mode != CODE_FILTER_OFF

        if mono:
            text = self.code_body(message, mode)
            if not text:
                return "", None  # no code in the source — skip the message
            # A code filter rebuilds the body from scratch, so the original
            # entities describe text that no longer exists.
            rich = _Rich(text, None)
        else:
            text = base_text or ""
            entities = getattr(message, "entities", None) if message is not None else None
            if text and plan_has(plan_name, F_HIDDEN_LINKS) and settings.get("disable_hidden_links"):
                # Revealing hidden links rewrites the text wholesale, so the
                # entities cannot follow it.
                text = self._reveal_hidden_links(text, entities)
                entities = None
            rich = _Rich(text, entities)

        if rich.text:
            self._apply_replacements(rich, settings, plan_name)
            if not mono:
                # Trim and the blanket Remove toggles are deliberately NOT
                # applied to a code the filter just extracted: a gift code is
                # often a referral link or an @handle, and stripping it would
                # delete the very thing the user asked for.
                self._apply_trim(rich, settings, plan_name)
                self._apply_removals(rich, settings, plan_name)

        text = rich.text.strip()
        if mono and not text:
            return "", None  # extracted code was blank

        header, footer = self._header_footer_for(settings, plan_name, dest_raw)

        if mono:
            # Always re-sent as monospace, whichever format it came from, so
            # subscribers can tap-to-copy the code in the destination.
            parts = []
            if header:
                parts.append(html_lib.escape(header))
            parts.append(f"<code>{html_lib.escape(text)}</code>")
            if footer:
                parts.append(html_lib.escape(footer))
            self._last_entities = None
            return "\n\n".join(parts), "html"

        parts = []
        if header:
            parts.append(header)
        if text:
            parts.append(text)
        if footer:
            parts.append(footer)

        # Entity offsets are relative to the body, so a header prefix shifts
        # them all by exactly its length. Everything else was already tracked
        # by _Rich as the text was edited.
        shift = 0
        if header:
            shift = _py_to_u16(f"{header}\n\n", len(header) + 2)
        stripped = rich.text.lstrip()
        if stripped != rich.text:
            shift -= _py_to_u16(rich.text, len(rich.text) - len(stripped))
        self._last_entities = _shift_entities(rich.entities(), shift)

        return "\n\n".join(parts), None

    def _clean_text(self, text: str, settings: dict, plan_name: str) -> str:
        """Backwards-compatible wrapper kept for any older call sites."""
        built, _ = self.build_text(None, text, settings, plan_name)
        return built

    # ==========================================
    # FILTERS
    # ==========================================

    def _topic_allowed(self, message: Message, settings: dict, plan_name: str, source_raw: int) -> bool:
        """Topics Forwarding — restricts a forum source to chosen topics.

        Empty selection means 'forward from all topics', which is also what
        every existing task has, so this can never silently stop an old task.
        """
        if not plan_has(plan_name, F_TOPICS):
            return True

        topics_cfg = settings.get("topics")
        selected: list = []
        if isinstance(topics_cfg, dict):
            selected = _as_list(topics_cfg.get(str(source_raw)))
        else:
            selected = _as_list(topics_cfg)

        if not selected:
            return True

        topic_id = message_topic_id(message)
        if topic_id is None:
            # Not a forum message. The user restricted topics, so a message
            # with no topic is out of scope.
            return False

        wanted = set()
        for item in selected:
            try:
                wanted.add(int(item))
            except (TypeError, ValueError):
                continue
        return int(topic_id) in wanted

    async def _sender_allowed(self, message: Message, settings: dict, plan_name: str) -> bool:
        """Sender Filter — only listed users' messages are forwarded."""
        if not plan_has(plan_name, F_SENDER_FILTER):
            return True
        allowed_senders = _as_list(settings.get("user_filter"))
        if not allowed_senders:
            return True

        sender = None
        with suppress(Exception):
            sender = await message.get_sender()
        sid = getattr(sender, "id", None)
        uname = (getattr(sender, "username", None) or "").lower()

        for entry in allowed_senders:
            token = str(entry).strip()
            if not token:
                continue
            if sid is not None and token == str(sid):
                return True
            if uname and token.lstrip("@").lower() == uname:
                return True
        return False

    def _keyword_allowed(self, raw_text: str, settings: dict, plan_name: str) -> bool:
        """Blacklist / Whitelist keyword filters."""
        lowered = (raw_text or "").lower()

        if plan_has(plan_name, F_WHITELIST):
            whitelist = [str(w).lower() for w in _as_list(settings.get("whitelist")) if str(w).strip()]
            if whitelist and not any(w in lowered for w in whitelist):
                return False

        if plan_has(plan_name, F_BLACKLIST):
            blacklist = [str(b).lower() for b in _as_list(settings.get("blacklist")) if str(b).strip()]
            if blacklist and any(b in lowered for b in blacklist):
                return False

        return True

    # ==========================================
    # NEW MESSAGE HANDLER
    # ==========================================

    def _lane_for(self, plan_name: str) -> asyncio.Semaphore:
        return self._lanes.get((plan_name or "free").lower(), self._lanes["free"])

    async def _on_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
        # Read the plan BEFORE queueing so a paying user never waits in the
        # free lane just to find out which lane they belong in.
        plan_name = "free"
        user = None
        with suppress(Exception):
            user = await self.db.get_user(user_id)
            if user is not None:
                plan_name = str(user["plan"] or "free")

        # Honour any pending anti-ban cooldown for this account before queueing.
        cooldown = self._cooldowns.get(user_id)
        if cooldown:
            wait = cooldown - asyncio.get_running_loop().time()
            if wait > 0:
                await asyncio.sleep(min(wait, 60))
            self._cooldowns.pop(user_id, None)

        async with self._lane_for(plan_name):
            try:
                # The user row is passed straight through: reading it again
                # inside meant two identical database round-trips for every
                # single incoming message.
                await self._process_new_message(event, user_id, user)
            except Exception:
                # A crash here would be swallowed by Telethon with a stack trace
                # nobody reads. Log it loudly with context instead.
                logger.exception("Unhandled error while forwarding for user %s", user_id)

    async def _process_new_message(
        self, event: events.NewMessage.Event, user_id: int, user=None,
    ) -> None:
        """Triggered when the user's account receives a new message in any chat."""
        message: Message = event.message
        source_raw = raw_peer_id(event.chat_id)
        if source_raw is None:
            return

        # A Message received from an event may only contain a bare peer ID.
        # Native forwarding needs the source entity/access hash explicitly.
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

        if user is None:
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

            sources = self._json_field(task["sources"], [])
            source_ids = {raw_peer_id(s.get("id")) for s in sources if isinstance(s, dict)}
            if source_raw not in source_ids:
                continue

            settings = self._json_field(task["settings"], {})
            if not isinstance(settings, dict):
                settings = {}

            # --- FILTERS ---
            if not self._topic_allowed(message, settings, plan_name, source_raw):
                continue
            if not await self._sender_allowed(message, settings, plan_name):
                continue
            if not self._keyword_allowed(message.raw_text or "", settings, plan_name):
                continue

            # --- LIMITS ---
            if plan.daily_messages and usage >= plan.daily_messages:
                # Tell the user ONCE per day. Previously this skipped silently,
                # which looks identical to the bot being broken.
                if await self.db.should_send_limit_notice(user_id):
                    await self._warn_user(
                        user_id, "limit_reached_notice", cap=plan.daily_messages,
                    )
                continue

            destinations = [d for d in self._json_field(task["destinations"], []) if isinstance(d, dict)]
            if not destinations:
                continue

            # CODE FILTER: forward ONLY what the source marked as monospace
            # and/or spoiler. A message with no matching content is skipped
            # entirely — nothing is sent and no quota is consumed.
            code_mode = self.code_filter_for(settings, plan_name)
            mono_on = code_mode != CODE_FILTER_OFF
            if mono_on and not self.code_body(message, code_mode):
                continue

            stored_file = await self._resolve_stored_file(user_id, settings, plan_name)
            # With the code filter on the user wants the code only, so media is
            # deliberately dropped rather than sent alongside it.
            media_file = (
                None if mono_on
                else await self._prepare_media(client, message, settings, plan_name)
            )

            # Speed controls
            dest_delay = (
                _preset_seconds(DELAY_PRESETS, settings.get("delay_timer"))
                if plan_has(plan_name, F_DELAY_TIMER) else 0.0
            )
            antiban_delay = (
                _preset_seconds(ANTIBAN_PRESETS, settings.get("antiban_speed"))
                if plan_has(plan_name, F_ANTIBAN) else 0.0
            )

            # Link Preview toggle. Default: mirror whatever the source had.
            if plan_has(plan_name, F_LINK_PREVIEW) and settings.get("link_preview") is not None:
                link_preview = bool(settings.get("link_preview"))
            else:
                link_preview = bool(message.web_preview)

            # Free plan uses a native forward, which keeps the "Forwarded from"
            # tag. Every paid tier gets a clean copy — that IS "No BOT Watermark".
            clean_copy = plan_has(plan_name, F_NO_WATERMARK)

            # Daily cap is applied UP FRONT so the parallel path can never
            # overshoot it mid-flight.
            if plan.daily_messages:
                remaining = max(0, plan.daily_messages - usage)
                if remaining <= 0:
                    continue
                destinations = destinations[:remaining]

            # The stored file is uploaded ONCE; every later destination reuses
            # the returned file_id. Re-uploading the same file per destination
            # meant a 5 MB attachment was sent 50 times for one post.
            shared_file_id = None
            edit_rows: list[tuple] = []
            results: list[tuple[int, object]] = []

            async def _deliver(dest: dict):
                """Sends one copy. Returns (dest_raw, sent_msg) or None."""
                nonlocal shared_file_id
                new_text, parse_mode, entities = "", None, None
                if dest.get("id") is None:
                    return None
                dest_raw = raw_peer_id(dest.get("id"))
                if dest_raw is None or dest_raw == source_raw:
                    return None  # never send a chat's messages back into itself
                dest_peer = await self._resolve_peer(client, user_id, dest)
                if dest_peer is None:
                    return None

                try:
                    if not clean_copy:
                        forward_kwargs = {}
                        if source_entity is not None:
                            forward_kwargs["from_peer"] = source_entity
                        sent_msg = await client.forward_messages(dest_peer, message, **forward_kwargs)
                    else:
                        new_text, parse_mode = self.build_text(
                            message, message.message or "", settings, plan_name, dest_raw
                        )
                        if not new_text and media_file is None:
                            return None  # nothing to send (e.g. service message)
                        # Carry the source formatting through when it is safe.
                        # This includes CUSTOM (animated) emoji, which live in
                        # the entities and are lost entirely without them.
                        entities = self._last_entities

                        payload = media_file
                        if isinstance(payload, io.BytesIO):
                            # A BytesIO can only be read once, so each parallel
                            # send needs its own view of the same bytes.
                            payload = io.BytesIO(payload.getvalue())
                            payload.name = getattr(media_file, "name", "photo.jpg")
                        sent_msg = await client.send_message(
                            dest_peer,
                            message=new_text,
                            file=payload,
                            link_preview=link_preview,
                            parse_mode=parse_mode,
                            formatting_entities=entities,
                        )
                except errors.FloodWaitError as fw:
                    # Temporary rate limit, NOT a broken destination. Wait it
                    # out once and repeat the EXACT same send — the previous
                    # build retried with a stripped-down message that dropped
                    # the media and all formatting, so a rate-limited post
                    # silently arrived degraded.
                    wait = int(getattr(fw, "seconds", 0) or 0)
                    logger.warning(
                        "FLOODWAIT %ss for user %s task %s dest %s — Telegram is rate limiting",
                        wait, user_id, task["id"], dest_raw,
                    )
                    self._floodwaits += 1
                    if wait > 300:
                        return None  # too long to hold a slot for
                    await asyncio.sleep(wait + 1)
                    try:
                        if not clean_copy:
                            forward_kwargs = {}
                            if source_entity is not None:
                                forward_kwargs["from_peer"] = source_entity
                            sent_msg = await client.forward_messages(
                                dest_peer, message, **forward_kwargs,
                            )
                        else:
                            retry_payload = media_file
                            if isinstance(retry_payload, io.BytesIO):
                                retry_payload = io.BytesIO(media_file.getvalue())
                                retry_payload.name = getattr(media_file, "name", "photo.jpg")
                            sent_msg = await client.send_message(
                                dest_peer,
                                message=new_text,
                                file=retry_payload,
                                link_preview=link_preview,
                                parse_mode=parse_mode,
                                formatting_entities=entities,
                            )
                    except Exception as e2:
                        logger.warning(
                            f"Task {task['id']} retry after FloodWait failed for {dest_raw}: {e2}"
                        )
                        return None
                except Exception as e:
                    text = str(e)
                    # A protected source is a permanent, explainable condition —
                    # not the "channel deleted / you were removed" story the
                    # generic warning tells. Report it accurately instead.
                    if "protected chat" in text.lower():
                        logger.warning(
                            "PROTECTED SOURCE: task %s user %s cannot copy from source %s",
                            task["id"], user_id, source_raw,
                        )
                        if not self._protected_reported.get((int(task["id"]), source_raw)):
                            self._protected_reported[(int(task["id"]), source_raw)] = True
                            await self._warn_user(
                                user_id, "protected_source_blocked",
                                task=str(task["task_name"]),
                            )
                        return None
                    # Service messages and deleted posts cannot be forwarded.
                    # These are normal events, not failures — never warn.
                    if "message ID is invalid" in text or "MESSAGE_ID_INVALID" in text:
                        logger.info(
                            "Skipping unforwardable message %s for task %s",
                            message.id, task["id"],
                        )
                        return None
                    logger.warning(
                        f"Task {task['id']} failed to send to {dest_raw} for user {user_id}: {e}"
                    )
                    fail_key = (int(task["id"]), dest_raw)
                    count = self._dest_failures.get(fail_key, 0) + 1
                    self._dest_failures[fail_key] = count
                    if count >= 3 and fail_key not in self._dest_reported:
                        self._dest_reported.add(fail_key)
                        await self._warn_user(
                            user_id, "destination_failed",
                            task=str(task["task_name"]),
                            dest=str(dest.get("title") or dest.get("username") or dest_raw),
                        )
                    return None

                if isinstance(sent_msg, list):
                    sent_msg = sent_msg[0] if sent_msg else None
                if not sent_msg:
                    return None

                self._remember_send(dest_raw, sent_msg.id)
                fail_key = (int(task["id"]), dest_raw)
                self._dest_failures.pop(fail_key, None)
                self._dest_reported.discard(fail_key)

                # Attach Custom File — upload once, then reuse the file_id.
                if stored_file is not None:
                    with suppress(Exception):
                        to_send = shared_file_id or str(stored_file["local_path"])
                        extra = await client.send_file(dest_peer, to_send)
                        if extra is not None:
                            extra_msg = extra[0] if isinstance(extra, list) else extra
                            self._remember_send(dest_raw, extra_msg.id)
                            if shared_file_id is None and getattr(extra_msg, "media", None):
                                shared_file_id = extra_msg.media

                # Auto Delete and Auto Reaction are fire-and-forget. Awaiting a
                # reaction added a full round-trip to EVERY destination for
                # something the user never waits on.
                if plan_has(plan_name, F_AUTO_DELETE):
                    try:
                        auto_delete_secs = int(settings.get("auto_delete_seconds") or 0)
                    except (TypeError, ValueError):
                        auto_delete_secs = 0
                    if auto_delete_secs > 0:
                        asyncio.create_task(
                            self._auto_delete(client, dest_peer, sent_msg.id, auto_delete_secs)
                        )

                asyncio.create_task(self._maybe_react(
                    client, settings, plan_name, "destination", dest_peer, sent_msg.id
                ))
                return dest_raw, sent_msg

            if dest_delay:
                # The user asked for a gap between targets, so respect it exactly.
                for index, dest in enumerate(destinations):
                    if index:
                        await asyncio.sleep(dest_delay)
                    got = await _deliver(dest)
                    if got:
                        results.append(got)
            else:
                # No delay configured: send in small parallel batches. Capped
                # low on purpose — firing all 50 at once is what triggers
                # Telegram FloodWait and risks the user's account.
                batch = PARALLEL_SENDS
                for i in range(0, len(destinations), batch):
                    chunk = destinations[i:i + batch]
                    done = await asyncio.gather(
                        *(_deliver(d) for d in chunk), return_exceptions=True,
                    )
                    for item in done:
                        if isinstance(item, tuple):
                            results.append(item)

            sent_any = bool(results)

            if sent_any:
                # One database round-trip for the whole fan-out instead of
                # three per destination.
                await self.db.increment_usage_bulk(user_id, int(task["id"]), len(results))
                usage += len(results)

                if self._edit_sync_enabled(settings, plan_name):
                    edit_rows = [
                        (int(task["id"]), user_id, source_raw, int(message.id),
                         int(dest_raw), int(sent_msg.id))
                        for dest_raw, sent_msg in results
                    ]
                    await self.db.record_sent_messages(edit_rows)

                # Auto Reaction on the source message — once per task
                with suppress(Exception):
                    asyncio.create_task(self._maybe_react(
                        client, settings, plan_name, "source",
                        await event.get_input_chat(), message.id,
                    ))

            if sent_any and antiban_delay:
                # Anti-Ban Speed: pause before this account sends anything again.
                # The wait is scheduled OUTSIDE the lane. Sleeping while holding
                # a lane slot meant one user's 8-second anti-ban setting also
                # stalled every other user on the same plan.
                self._cooldowns[user_id] = (
                    asyncio.get_running_loop().time() + antiban_delay
                )

    async def _warn_user(self, user_id: int, key: str, **kwargs) -> None:
        """Best-effort user warning. Never raises — a blocked user or a network
        blip must not disturb forwarding."""
        if self.bot is None:
            return
        try:
            from .locales import language_for, t
            user = await self.db.get_user(user_id)
            language = language_for(user["preferred_language"]) if user else "en"
            await self.bot.send_message(user_id, t(language, key, **kwargs), parse_mode="HTML")
        except Exception as exc:
            logger.debug("Could not warn user %s (%s): %s", user_id, key, exc)

    # ==========================================
    # EDIT SYNC
    # ==========================================

    def _edit_sync_enabled(self, settings: dict, plan_name: str) -> bool:
        """Post Edit Sync is a toggle on Gold and automatic on Platinum.

        Platinum's tree advertises "Automatic Post Edit Sync", so it defaults
        ON there; Gold advertises it as ON/OFF and defaults OFF.
        """
        if not plan_has(plan_name, F_POST_EDIT_SYNC):
            return False
        value = settings.get("post_edit_sync")
        if value is None:
            return plan_name == "platinum"
        return bool(value)

    async def _on_message_edited(self, event: events.MessageEdited.Event, user_id: int) -> None:
        """Mirrors an edit in the source chat onto every copy we sent."""
        try:
            message: Message = event.message
            source_raw = raw_peer_id(event.chat_id)
            if source_raw is None:
                return

            copies = await self.db.get_sent_copies(source_raw, int(message.id))
            if not copies:
                return

            user = await self.db.get_user(user_id)
            if not user:
                return
            plan_name = str(user["plan"] or "free")
            if not plan_has(plan_name, F_POST_EDIT_SYNC):
                return

            client = self.clients.get(user_id)
            if not client:
                return

            # Group the copies by the task that produced them, so each one is
            # re-rendered with the exact settings that were applied originally.
            by_task: dict[int, list] = {}
            for row in copies:
                if int(row["user_id"] or 0) != user_id:
                    continue
                by_task.setdefault(int(row["task_id"]), []).append(row)

            for task_id, rows in by_task.items():
                task = await self.db.get_task(task_id)
                if not task or int(task["user_id"]) != user_id:
                    continue

                settings = self._json_field(task["settings"], {})
                if not self._edit_sync_enabled(settings, plan_name):
                    continue

                dest_refs = self._json_field(task["destinations"], [])
                by_raw = {
                    raw_peer_id(d.get("id")): d
                    for d in dest_refs
                    if isinstance(d, dict)
                }

                for row in rows:
                    dest_raw = int(row["dest_chat_id"])
                    ref = by_raw.get(dest_raw)
                    if ref is None:
                        continue
                    dest_peer = await self._resolve_peer(client, user_id, ref)
                    if dest_peer is None:
                        continue

                    new_text, parse_mode = self.build_text(
                        message, message.message or "", settings, plan_name, dest_raw
                    )
                    # Empty means the code filter found nothing in the edited
                    # version; leave the existing copy alone rather than
                    # blanking it out.
                    if not new_text:
                        continue
                    try:
                        await client.edit_message(
                            dest_peer, int(row["dest_message_id"]),
                            text=new_text, parse_mode=parse_mode,
                        )
                    except Exception as e:
                        # Telegram refuses edits older than 48h and rejects
                        # "content unchanged" — both are normal, not errors.
                        logger.debug(
                            f"Edit sync skipped for {row['dest_message_id']} in {dest_raw}: {e}"
                        )
        except Exception:
            logger.exception("Unhandled error in edit sync for user %s", user_id)

    # ==========================================
    # AUTO REACTION
    # ==========================================

    async def _maybe_react(
        self, client: TelegramClient, settings: dict, plan_name: str,
        target: str, peer, message_id: int,
    ) -> None:
        """Auto Reaction System.

        Uses the already-connected engine client rather than opening a new one
        per message. Entirely best-effort: a chat that disallows the emoji, or
        a rate limit, must never affect the forward that already succeeded.
        """
        if not plan_has(plan_name, F_AUTO_REACTION) or peer is None:
            return
        config = _as_dict(settings.get("auto_reaction"))
        if not config.get("enabled"):
            return
        if str(config.get("target") or "source") != target:
            return

        emoji = str(config.get("emoji") or DEFAULT_REACTION_EMOJI)
        try:
            await client(functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=int(message_id),
                big=bool(config.get("big", False)),
                reaction=[types.ReactionEmoji(emoticon=emoji)],
            ))
        except Exception as e:
            logger.debug(f"Auto reaction {emoji!r} skipped on {message_id}: {e}")

    # ==========================================
    # MEDIA / WATERMARK
    # ==========================================

    async def _prepare_media(
        self, client: TelegramClient, message: Message, settings: dict, plan_name: str,
    ):
        """Returns the media to send: the original, or a watermarked copy."""
        media_file = message.media
        if isinstance(media_file, MessageMediaWebPage):
            # Link previews are not sendable media; the URL lives in the text.
            return None

        if not plan_has(plan_name, F_WATERMARK_IMAGE) or not settings.get("watermark"):
            return media_file
        if not isinstance(message.media, MessageMediaPhoto):
            return media_file

        watermark_text = str(settings.get("watermark_text") or "Forwarded via DealsKoti")
        style = {"position": "bottom_right", "size": "medium", "opacity": 70}
        if plan_has(plan_name, F_WATERMARK_STYLE):
            configured = _as_dict(settings.get("watermark_style"))
            if configured.get("position") in WATERMARK_POSITIONS:
                style["position"] = configured["position"]
            if configured.get("size") in WATERMARK_SIZES:
                style["size"] = configured["size"]
            try:
                opacity = int(configured.get("opacity", style["opacity"]))
                if opacity in WATERMARK_OPACITIES:
                    style["opacity"] = opacity
            except (TypeError, ValueError):
                pass

        watermarked = await self._apply_image_watermark(
            client, message, watermark_text, style, max_image_bytes=10 * 1024 * 1024
        )
        if not watermarked:
            return media_file

        buffer = io.BytesIO(watermarked)
        buffer.name = "photo.jpg"  # Telethon needs a name to infer the type
        return buffer

    async def _apply_image_watermark(
        self,
        client: TelegramClient,
        message: Message,
        watermark_text: str,
        style: dict,
        max_image_bytes: int,
    ) -> bytes | None:
        """Draws the watermark onto a photo and returns PNG bytes.

        Returns None if there is no downloadable photo or processing fails, in
        which case the caller falls back to sending the original image.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            logger.warning("Pillow not available; skipping image watermark")
            return None

        # Download the full-size photo. Passing thumb=0 would fetch the smallest
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

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        width, height = img.size
        divisor = WATERMARK_SIZES.get(str(style.get("size")), WATERMARK_SIZES["medium"])
        font_size = max(18, min(96, height // divisor))

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
        padding, margin = 8, 12
        pill_w, pill_h = text_w + padding * 2, text_h + padding * 2

        position = str(style.get("position") or "bottom_right")
        if position == "bottom_left":
            pill_x, pill_y = margin, height - pill_h - margin
        elif position == "top_right":
            pill_x, pill_y = width - pill_w - margin, margin
        elif position == "top_left":
            pill_x, pill_y = margin, margin
        elif position == "center":
            pill_x, pill_y = (width - pill_w) // 2, (height - pill_h) // 2
        else:  # bottom_right
            pill_x, pill_y = width - pill_w - margin, height - pill_h - margin

        # Keep the pill on-screen even if the text is wider than the image.
        pill_x = max(0, min(pill_x, max(0, width - pill_w)))
        pill_y = max(0, min(pill_y, max(0, height - pill_h)))

        try:
            opacity_pct = int(style.get("opacity", 70))
        except (TypeError, ValueError):
            opacity_pct = 70
        opacity_pct = max(10, min(100, opacity_pct))
        text_alpha = int(255 * opacity_pct / 100)
        pill_alpha = int(140 * opacity_pct / 100)

        draw.rounded_rectangle(
            [(pill_x, pill_y), (pill_x + pill_w, pill_y + pill_h)],
            radius=8,
            fill=(0, 0, 0, pill_alpha),
        )
        draw.text(
            (pill_x + padding, pill_y + padding - bbox[1]),
            watermark_text,
            font=font,
            fill=(255, 255, 255, text_alpha),
        )

        out = Image.alpha_composite(img, overlay).convert("RGB")
        buf = io.BytesIO()
        # JPEG, not PNG. PNG is a lossless format meant for graphics: on a real
        # photo it produced files ~3.5x LARGER than the original and took ~20x
        # longer to encode, which was the single biggest cause of slow
        # watermarked forwards. JPEG at 90 is visually indistinguishable here.
        out.save(buf, format="JPEG", quality=90, optimize=True, progressive=True)
        return buf.getvalue()

    # ==========================================
    # STORED FILE (ATTACH CUSTOM FILE)
    # ==========================================

    async def _resolve_stored_file(self, user_id: int, settings: dict, plan_name: str):
        """Returns the user's stored file record if it should be attached.

        Railway's filesystem is ephemeral, so a missing local copy is restored
        from the storage channel. Existence is re-checked at most once a minute
        rather than on every message, to keep forwarding fast.
        """
        if not plan_has(plan_name, F_ATTACH_FILE):
            return None
        # Default OFF. Defaulting this ON meant that once a user uploaded a
        # single file it was silently attached to EVERY message of EVERY task
        # they had — heavy, expensive, and something they never asked for.
        # Attaching a file is now an explicit per-task choice.
        if not settings.get("attach_stored_file", False):
            return None

        stored_file = None
        with suppress(Exception):
            stored_file = await self.db.get_stored_file(user_id)
        if stored_file is None:
            return None

        local_path = stored_file["local_path"]
        if local_path and os.path.exists(str(local_path)):
            check = self._stored_file_checks.get(user_id)
            now_mono = asyncio.get_running_loop().time()
            if (
                check is None
                or check[0] != int(stored_file["id"])
                or now_mono - check[1] >= 60
            ):
                exists = True
                if self.bot_token and self.storage_channel_id and stored_file["channel_message_id"]:
                    exists = await self.telethon.media_exists_big(
                        self.bot_token,
                        self.storage_channel_id,
                        int(stored_file["channel_message_id"]),
                    )
                self._stored_file_checks[user_id] = (int(stored_file["id"]), now_mono, exists)
                if not exists:
                    with suppress(Exception):
                        os.remove(str(local_path))
                    await self.db.update_stored_file_path(user_id, None)
                    return None
            return stored_file

        # Local cache missing — try to restore it from the storage channel.
        channel_msg_id = stored_file["channel_message_id"]
        if not (self.bot_token and self.storage_channel_id and channel_msg_id):
            return None

        safe_name = os.path.basename(str(stored_file["file_name"] or "file.bin")).replace(chr(0), "_")
        restored_path = os.path.join("uploads", f"stored_{user_id}_{stored_file['id']}_{safe_name}")
        os.makedirs("uploads", exist_ok=True)
        restored = await self.telethon.download_media_big(
            self.bot_token, self.storage_channel_id, int(channel_msg_id), restored_path,
        )
        if not restored:
            return None

        await self.db.update_stored_file_path(user_id, restored_path)
        record = dict(stored_file)
        record["local_path"] = restored_path
        self._stored_file_checks[user_id] = (
            int(stored_file["id"]), asyncio.get_running_loop().time(), True,
        )
        return record

    # ==========================================
    # BULK TRANSFER (one-time history copy)
    # ==========================================
    # Copies a channel's EXISTING posts into another channel. Completely
    # separate from live forwarding: no task, no task settings, no daily-quota
    # accounting.
    #
    # Deliberately SLOW. Pushing thousands of old messages through an account
    # in a few minutes is exactly the pattern Telegram rate-limits and
    # restricts accounts for, so the pace is fixed low and cannot be raised
    # from the UI. A user's account is worth more than a faster transfer.

    TRANSFER_DELAY = 1.2          # seconds between messages
    TRANSFER_MEDIA_DELAY = 2.0    # media uploads are heavier, wait longer

    def transfer_state(self, user_id: int) -> dict | None:
        return self._transfers.get(user_id)

    def cancel_transfer(self, user_id: int) -> bool:
        state = self._transfers.get(user_id)
        if not state:
            return False
        state["cancel"] = True
        return True

    async def count_transfer_messages(
        self, user_id: int, source_ref: dict, since_days: int | None, limit: int | None,
    ) -> int:
        """How many messages the chosen range holds, so the confirmation can
        show a real number and a real time estimate."""
        client = self.clients.get(user_id)
        if client is None:
            return 0
        peer = await self._resolve_peer(client, user_id, source_ref)
        if peer is None:
            return 0
        try:
            if limit:
                total = await client.get_messages(peer, limit=1)
                return min(int(getattr(total, "total", 0) or 0), limit)
            if since_days is None:
                total = await client.get_messages(peer, limit=1)
                return int(getattr(total, "total", 0) or 0)
            cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
            count = 0
            async for _ in client.iter_messages(peer, offset_date=cutoff, reverse=True):
                count += 1
                if count >= 50000:
                    break
            return count
        except Exception as exc:
            logger.warning("Could not count transfer messages for %s: %s", user_id, exc)
            return 0

    async def run_bulk_transfer(
        self, user_id: int, source_ref: dict, dest_ref: dict,
        since_days: int | None, limit: int | None, progress_cb=None,
    ) -> dict:
        """Runs the copy. Returns a result summary.

        Only ONE transfer per user at a time — several at once from the same
        account is the fastest way to get it rate-limited.
        """
        if user_id in self._transfers:
            return {"error": "already_running"}

        client = self.clients.get(user_id)
        if client is None:
            return {"error": "not_connected"}

        source = await self._resolve_peer(client, user_id, source_ref)
        dest = await self._resolve_peer(client, user_id, dest_ref)
        if source is None or dest is None:
            return {"error": "peer_unresolved"}

        state = {"cancel": False, "sent": 0, "skipped": 0, "total": 0, "started": True}
        self._transfers[user_id] = state
        dest_raw = raw_peer_id(dest_ref.get("id"))

        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=since_days)
                if since_days is not None else None
            )
            if limit:
                # Newest-first, then reversed so the copy reads chronologically
                # in the destination rather than backwards.
                batch = await client.get_messages(source, limit=limit)
                messages = list(reversed(batch))
                iterator = _async_iter(messages)
            else:
                iterator = client.iter_messages(source, offset_date=cutoff, reverse=True)

            async for message in iterator:
                if state["cancel"]:
                    break
                if getattr(message, "action", None) is not None:
                    state["skipped"] += 1  # service message, cannot be copied
                    continue
                text = message.message or ""
                media = message.media
                if isinstance(media, MessageMediaWebPage):
                    media = None
                if not text and media is None:
                    state["skipped"] += 1
                    continue

                try:
                    sent = await client.send_message(
                        dest,
                        message=text,
                        file=media,
                        formatting_entities=getattr(message, "entities", None) or None,
                        link_preview=bool(message.web_preview),
                    )
                    if sent is not None:
                        sent_one = sent[0] if isinstance(sent, list) else sent
                        if dest_raw is not None:
                            self._remember_send(dest_raw, sent_one.id)
                    state["sent"] += 1
                except errors.FloodWaitError as fw:
                    wait = int(getattr(fw, "seconds", 0) or 0)
                    logger.warning("Bulk transfer FloodWait %ss for user %s", wait, user_id)
                    if wait > 600:
                        state["error"] = f"Telegram asked to wait {wait}s — stopping."
                        break
                    await asyncio.sleep(wait + 2)
                    continue
                except Exception as exc:
                    logger.info("Bulk transfer skipped a message for %s: %s", user_id, exc)
                    state["skipped"] += 1

                await asyncio.sleep(
                    self.TRANSFER_MEDIA_DELAY if media is not None else self.TRANSFER_DELAY
                )
                if progress_cb is not None and state["sent"] % 15 == 0:
                    with suppress(Exception):
                        await progress_cb(state)
        except Exception:
            logger.exception("Bulk transfer failed for user %s", user_id)
            state["error"] = "unexpected"
        finally:
            self._transfers.pop(user_id, None)

        state["cancelled"] = state["cancel"]
        return state

    # ==========================================
    # HELPERS
    # ==========================================

    @staticmethod
    def _json_field(value, default):
        """asyncpg returns JSONB as either a parsed object or a raw string
        depending on codec setup, so every read has to handle both."""
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            parsed = json.loads(value or ("[]" if isinstance(default, list) else "{}"))
        except (TypeError, ValueError):
            return default
        return parsed if isinstance(parsed, type(default)) else default

    async def _auto_delete(self, client: TelegramClient, chat_id, message_id: int, delay_seconds: int) -> None:
        """Background task to delete a forwarded message after X seconds."""
        await asyncio.sleep(delay_seconds)
        if client.is_connected():
            with suppress(Exception):
                await client.delete_messages(chat_id, message_id)
