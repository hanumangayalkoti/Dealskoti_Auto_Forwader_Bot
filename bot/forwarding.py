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
from contextlib import suppress

from telethon import TelegramClient, events, functions, types
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

    def _apply_replacements(self, text: str, settings: dict, plan_name: str) -> str:
        """Replace Words / Usernames / Links — the user's explicit swaps."""
        if plan_has(plan_name, F_REPLACE_WORDS):
            # "replace" is the legacy key; kept so old tasks keep working.
            for key in ("replace", "replace_words"):
                for old_word, new_word in _as_dict(settings.get(key)).items():
                    if old_word:
                        text = text.replace(str(old_word), str(new_word))

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

                text = USERNAME_RE.sub(_sub_username, text)

        if plan_has(plan_name, F_REPLACE_LINKS):
            for old_link, new_link in _as_dict(settings.get("replace_links")).items():
                if old_link and str(old_link) in text:
                    text = text.replace(str(old_link), str(new_link))

        return text

    def _apply_trim(self, text: str, settings: dict, plan_name: str) -> str:
        """Trim Single Words/Lines.

        Each entry is removed wherever it appears. If removing it leaves a line
        empty, that whole line goes too — otherwise the message fills up with
        blank gaps where the trimmed words used to be.
        """
        if not plan_has(plan_name, F_TRIM_WORDS):
            return text
        words = [str(w).strip() for w in _as_list(settings.get("trim_words")) if str(w).strip()]
        if not words:
            return text

        for word in words:
            text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)

        kept = []
        for line in text.split("\n"):
            if line.strip():
                kept.append(line.rstrip())
            elif kept and kept[-1] != "":
                # Collapse runs of blank lines into a single one.
                kept.append("")
        return "\n".join(kept).strip("\n")

    def _apply_removals(self, text: str, settings: dict, plan_name: str) -> str:
        """Remove Usernames / Remove Links — blanket strip toggles.

        Runs AFTER replacements, so a user who set up a replacement gets their
        swap applied first. Header/footer are added later and are never touched
        by this, so the user's own handles and links always survive.
        """
        if plan_has(plan_name, F_REMOVE_USERNAMES) and settings.get("remove_usernames"):
            text = USERNAME_RE.sub("", text)

        if plan_has(plan_name, F_REMOVE_LINKS) and settings.get("remove_links"):
            text = URL_RE.sub("", text)

        if settings.get("remove_usernames") or settings.get("remove_links"):
            # Tidy up the double spaces / empty lines the strip leaves behind.
            text = re.sub(r"[ \t]{2,}", " ", text)
            text = "\n".join(line.rstrip() for line in text.split("\n"))
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

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

        if mono:
            text = self.code_body(message, mode)
            if not text:
                return "", None  # no code in the source — skip the message
        else:
            text = base_text or ""
            if text and plan_has(plan_name, F_HIDDEN_LINKS) and settings.get("disable_hidden_links"):
                entities = getattr(message, "entities", None) if message is not None else None
                text = self._reveal_hidden_links(text, entities)

        if text:
            text = self._apply_replacements(text, settings, plan_name)
            text = self._apply_trim(text, settings, plan_name)
            text = self._apply_removals(text, settings, plan_name)

        if mono and not text.strip():
            # The cleanup rules stripped the extracted code away entirely.
            return "", None

        header, footer = self._header_footer_for(settings, plan_name, dest_raw)

        if mono:
            # Always re-sent as monospace, whichever format it came from, so
            # subscribers can tap-to-copy the code in the destination.
            # Header/footer stay plain, otherwise the user's own branding and
            # links would become unreadable and unclickable.
            parts = []
            if header:
                parts.append(html_lib.escape(header))
            parts.append(f"<code>{html_lib.escape(text)}</code>")
            if footer:
                parts.append(html_lib.escape(footer))
            return "\n\n".join(parts), "html"

        parts = []
        if header:
            parts.append(header)
        if text.strip():
            parts.append(text)
        if footer:
            parts.append(footer)
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

    async def _on_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
        async with self._message_semaphore:
            try:
                await self._process_new_message(event, user_id)
            except Exception:
                # A crash here would be swallowed by Telethon with a stack trace
                # nobody reads. Log it loudly with context instead.
                logger.exception("Unhandled error while forwarding for user %s", user_id)

    async def _process_new_message(self, event: events.NewMessage.Event, user_id: int) -> None:
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
                continue  # quota exceeded, ignore silently to prevent spam

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

            sent_any = False

            for index, dest in enumerate(destinations):
                if index and dest_delay:
                    await asyncio.sleep(dest_delay)
                if dest.get("id") is None:
                    continue
                dest_raw = raw_peer_id(dest.get("id"))
                if dest_raw is None or dest_raw == source_raw:
                    continue  # never send a chat's messages back into itself
                dest_peer = await self._resolve_peer(client, user_id, dest)
                if dest_peer is None:
                    continue

                sent_msg = None
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
                            continue  # nothing to send (e.g. service message)
                        if isinstance(media_file, io.BytesIO):
                            media_file.seek(0)
                        sent_msg = await client.send_message(
                            dest_peer,
                            message=new_text,
                            file=media_file,
                            link_preview=link_preview,
                            parse_mode=parse_mode,
                        )
                except Exception as e:
                    logger.warning(
                        f"Task {task['id']} failed to send to {dest_raw} for user {user_id}: {e}"
                    )
                    continue

                if isinstance(sent_msg, list):
                    sent_msg = sent_msg[0] if sent_msg else None
                if not sent_msg:
                    continue

                sent_any = True
                self._remember_send(dest_raw, sent_msg.id)
                # Only count usage on SUCCESS — failed sends do not consume quota
                await self.db.increment_usage(user_id)
                usage += 1

                # Post Edit Sync bookkeeping (DB-backed so it survives restarts)
                if self._edit_sync_enabled(settings, plan_name):
                    await self.db.record_sent_message(
                        int(task["id"]), user_id,
                        source_raw, int(message.id),
                        dest_raw, int(sent_msg.id),
                    )

                # Attach Custom File
                if stored_file is not None:
                    with suppress(Exception):
                        extra = await client.send_file(dest_peer, str(stored_file["local_path"]))
                        if extra is not None:
                            extra_msg = extra[0] if isinstance(extra, list) else extra
                            self._remember_send(dest_raw, extra_msg.id)

                # Auto Delete
                if plan_has(plan_name, F_AUTO_DELETE):
                    try:
                        auto_delete_secs = int(settings.get("auto_delete_seconds") or 0)
                    except (TypeError, ValueError):
                        auto_delete_secs = 0
                    if auto_delete_secs > 0:
                        asyncio.create_task(
                            self._auto_delete(client, dest_peer, sent_msg.id, auto_delete_secs)
                        )

                # Auto Reaction on the forwarded copy
                await self._maybe_react(
                    client, settings, plan_name, "destination", dest_peer, sent_msg.id
                )

                if plan.daily_messages and usage >= plan.daily_messages:
                    break

            # Auto Reaction on the source message — once per task, not per target
            if sent_any:
                await self._maybe_react(
                    client, settings, plan_name, "source", await event.get_input_chat(), message.id
                )

            if sent_any and antiban_delay:
                # Anti-Ban Speed: pause before this account sends anything again.
                await asyncio.sleep(antiban_delay)

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
        buffer.name = "photo.png"  # Telethon needs a name to infer the type
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
        out.save(buf, format="PNG", optimize=True)
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
        # Default ON so existing tasks keep working until explicitly turned off.
        if not settings.get("attach_stored_file", True):
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
