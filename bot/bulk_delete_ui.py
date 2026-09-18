"""
/bulk_delete — wipe the message history of a channel or group.

Deliberately a SEPARATE module: this is the only irreversible operation in the
bot, and keeping it out of settings_ui / billing_ui / forwarding means a change
here can never break message forwarding or payments.

NON-NEGOTIABLE RULES (do not "optimise" these away in a rewrite):
  1. Nothing is deleted before the user types DELETE in capitals. A button tap
     is never enough for an irreversible action.
  2. Only chats where the user genuinely holds the Delete Messages right are
     listed, and the right is re-checked immediately before deleting.
  3. Sessions are read through TelethonService, which decrypts them. Never
     touch the sessions table directly.
  4. One delete job per user, and never while a bulk transfer is running —
     two heavy jobs on one account is what earns a Telegram rate limit.
  5. Report the REAL deleted count. Never claim everything was deleted when
     Telegram refused part of it.

A NOTE ON THE CLIENT (a deliberate deviation from the original spec):
The spec asked for a fresh TelegramClient per job. The forwarding engine
already holds a live client for the same session, and running two clients on
one session concurrently is what produces
"authorization key used under two different IP addresses" — which kills the
session permanently and forces the user to /connect again. So the engine's
existing client is reused when present, and a temporary one is created (and
disconnected in a finally block) only when the engine has none.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat

from .config import Settings
from .db import Database
from .forwarding import ForwardingEngine
from .locales import language_for, t
from .plans import STYLE_BUY, STYLE_GO, plan_label, PLANS, F_BULK_DELETE, min_plan_for, plan_has
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.bulkdelete")

router = Router(name="dealskoti-bulk-delete")

# Telegram deletes at most 100 messages per call — this is their limit, not ours.
DELETE_BATCH = 100
# Gap between batches. Low enough to feel fast (about 200 messages/second),
# high enough that Telegram does not immediately rate-limit the account.
BATCH_DELAY = 0.3
# Backed off to this after a FloodWait, so a struggling account is not pushed.
SLOW_DELAY = 1.5
# The typed confirmation expires, so a half-finished flow cannot be completed
# hours later by accident.
CONFIRM_TIMEOUT = 120
PAGE_SIZE = 8

CONFIRM_WORD = "DELETE"


class BulkDeleteStates(StatesGroup):
    waiting_confirm = State()
    waiting_date = State()
    waiting_range_start = State()
    waiting_range_end = State()
    waiting_keyword = State()
    waiting_user = State()


# ==========================================
# FILTERS
# ==========================================
# Each filter narrows WHAT gets deleted. The label is carried through to the
# confirmation and the final report, so the admin and the user can both see
# exactly what was removed rather than a bare count.

DATE_INPUT_RE = re.compile(r"^(\d{1,2})([a-z]{3})(\d{2,4})$", re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_short_date(raw: str):
    """Parses 10sep26 -> date(2026, 9, 10). Returns None if unreadable."""
    match = DATE_INPUT_RE.match(raw.strip())
    if not match:
        return None
    day, month_name, year = match.groups()
    month = MONTHS.get(month_name.lower())
    if month is None:
        return None
    year_num = int(year)
    if year_num < 100:
        year_num += 2000
    try:
        return datetime(year_num, month, int(day), tzinfo=timezone.utc)
    except ValueError:
        return None


# user_id -> live job state {cancel, deleted, failed, chat, started}
_JOBS: dict[int, dict] = {}


def job_state(user_id: int) -> dict | None:
    return _JOBS.get(user_id)


# ==========================================
# LOCAL HELPERS
# ==========================================

def safe_html(text) -> str:
    return html.escape(str(text))


def safe_t(lang: str, key: str, **kwargs) -> str:
    try:
        return t(lang, key, **kwargs)
    except Exception:
        logger.warning("Missing translation key %r for language %r", key, lang)
        return f"[{key}]"


async def _allowed(db: Database, user_id: int) -> bool:
    """Is bulk delete still available to this user?

    Re-checked on EVERY callback, not just on the command. A button from a
    week ago is still tappable, so a plan that has since expired would
    otherwise keep working through stale keyboards.
    """
    user = await db.get_user(user_id)
    return plan_has(str(user["plan"]) if user else "free", F_BULK_DELETE)


async def _lang(db: Database, user_id: int) -> str:
    user = await db.get_user(user_id)
    return language_for(user["preferred_language"]) if user else "en"


async def _show(message_obj, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        try:
            await message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
    await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")


SPINNER_FRAMES = ("▰▱▱▱▱▱▱▱", "▰▰▱▱▱▱▱▱", "▰▰▰▱▱▱▱▱", "▰▰▰▰▱▱▱▱",
                  "▱▰▰▰▰▱▱▱", "▱▱▰▰▰▰▱▱", "▱▱▱▰▰▰▰▱", "▱▱▱▱▰▰▰▰",
                  "▱▱▱▱▱▰▰▰", "▱▱▱▱▱▱▰▰", "▱▱▱▱▱▱▱▰", "▱▱▱▱▱▱▱▱")


@asynccontextmanager
async def _typing(bot, chat_id: int):
    """Keeps the "typing…" indicator alive for a long job.

    Telegram clears it after ~5 seconds, so it has to be re-sent. Paired with
    the Loader, this is what every other wait in the bot looks like.
    """
    stop = asyncio.Event()

    async def keep():
        while not stop.is_set():
            with suppress(Exception):
                await bot.send_chat_action(chat_id, "typing")
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=4.0)

    task = asyncio.create_task(keep())
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task


class Loader:
    """Keeps a loading message moving while slow work happens.

    Shows a real percentage when the total is known and a moving bar when it
    is not — a made-up percentage would sit at 99% and look broken.

    Telegram drops edits sent faster than about one per second, so updates are
    paced rather than pushed.
    """

    def __init__(self, message: Message, key: str, language: str,
                 total: int = 0, interval: float = 1.1, **extra):
        self.message, self.key, self.language = message, key, language
        self.total, self.interval, self.extra = total, interval, extra
        self.done = 0
        self._frame = 0
        self._task: asyncio.Task | None = None

    def _render(self) -> str:
        if self.total > 0:
            pct = max(0, min(100, int(100 * self.done / self.total)))
            bar = f"{_bar(self.done, self.total)}  {pct}%"
        else:
            bar = SPINNER_FRAMES[self._frame % len(SPINNER_FRAMES)]
        return safe_t(
            self.language, self.key, bar=bar,
            found=f"{self.done:,}", done=f"{self.done:,}",
            total=f"{self.total:,}", **self.extra,
        )

    async def _paint(self) -> None:
        with suppress(Exception):
            await self.message.edit_text(self._render(), parse_mode="HTML")

    async def _run(self) -> None:
        while True:
            self._frame += 1
            await self._paint()
            await asyncio.sleep(self.interval)

    def advance(self, by: int = 1, **extra) -> None:
        self.done += by
        if extra:
            self.extra.update(extra)

    async def __aenter__(self) -> "Loader":
        await self._paint()
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task


def _bar(done: int, total: int, width: int = 12) -> str:
    if total <= 0:
        return "▱" * width
    filled = max(0, min(width, int(width * done / total)))
    return "▰" * filled + "▱" * (width - filled)


def _took(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


# ==========================================
# CLIENT ACCESS
# ==========================================

async def _acquire_client(
    user_id: int, telethon: TelethonService, forwarding: ForwardingEngine,
) -> tuple[TelegramClient | None, bool]:
    """Returns (client, we_own_it).

    Prefers the forwarding engine's already-connected client. Opening a second
    client on the same session can permanently invalidate it, which would log
    the user out of the bot entirely.
    """
    live = forwarding.clients.get(user_id)
    if live is not None and live.is_connected():
        return live, False

    session_string = await telethon._get_session_string(user_id)
    if not session_string:
        return None, False
    client = TelegramClient(StringSession(session_string), telethon.api_id, telethon.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        with suppress(Exception):
            await client.disconnect()
        return None, False
    return client, True


async def _release_client(client: TelegramClient | None, owned: bool) -> None:
    """Disconnects ONLY a client this module created. The engine's client is
    left alone — disconnecting it would silently stop the user's forwarding."""
    if client is not None and owned:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception as exc:
            logger.debug("Could not disconnect temporary client: %s", exc)


async def _can_delete(client: TelegramClient, entity) -> bool:
    """True only when the user may delete OTHER people's messages here.

    Listing a chat the user cannot clean just produces a job that fails
    halfway, so this is checked before listing and again before deleting.
    """
    try:
        perms = await client.get_permissions(entity, "me")
    except Exception:
        return False
    if getattr(perms, "is_creator", False):
        return True
    return bool(getattr(perms, "delete_messages", False))


# ==========================================
# STEP 1 — ENTRY
# ==========================================

def _intro_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Channels", callback_data="bd:list:ch:0"),
         InlineKeyboardButton(text="👥 Groups", callback_data="bd:list:gr:0")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="menu:home")],
    ])


@router.message(Command("bulk_delete", "bulkdelete"))
async def bulk_delete_command(
    message: Message, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    language = await _lang(db, message.from_user.id)
    user = await db.get_user(message.from_user.id)
    plan_name = str(user["plan"]) if user else "free"

    if not plan_has(plan_name, F_BULK_DELETE):
        required = PLANS.get(min_plan_for(F_BULK_DELETE), PLANS["gold"]).name
        return await message.answer(
            safe_t(language, "bd_locked"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"💎 Upgrade to {required}", callback_data="menu:plans", style=STYLE_BUY)],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )

    if not await db.has_active_session(message.from_user.id):
        # Reuse the bot's own connect flow rather than inventing another one.
        return await message.answer(
            safe_t(language, "connect_required"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect", style=STYLE_GO)],
                [InlineKeyboardButton(text="🔐 Why is this needed?", callback_data="why:connect")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )

    running = _JOBS.get(message.from_user.id)
    if running:
        return await message.answer(
            safe_t(language, "bd_busy", deleted=f"{running.get('deleted', 0):,}"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏹️ Stop", callback_data="bd:stop")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )

    if forwarding.transfer_state(message.from_user.id):
        return await message.answer(
            safe_t(language, "bd_transfer_busy"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )

    await message.answer(safe_t(language, "bd_intro"), reply_markup=_intro_markup(),
                         parse_mode="HTML")


# ==========================================
# STEP 2 — CHAT LIST
# ==========================================

async def _collect_chats(
    client: TelegramClient, kind: str, limit: int = 400, progress_cb=None,
) -> list[dict]:
    """The user's channels or groups where they can delete messages.

    Reports progress as it goes: checking permissions on a few hundred chats
    takes real time, and a frozen screen looks like a hung bot.
    """
    found: list[dict] = []
    scanned = 0
    async for dialog in client.iter_dialogs(limit=limit):
        scanned += 1
        if progress_cb is not None and scanned % 5 == 0:
            with suppress(Exception):
                progress_cb(len(found))
        entity = dialog.entity
        if kind == "ch":
            ok_type = isinstance(entity, Channel) and not getattr(entity, "megagroup", False)
        else:
            ok_type = isinstance(entity, Chat) or (
                isinstance(entity, Channel) and getattr(entity, "megagroup", False)
            )
        if not ok_type:
            continue
        if not await _can_delete(client, entity):
            continue
        found.append({
            "id": entity.id,
            "title": dialog.title or getattr(entity, "username", None) or str(entity.id),
        })
        if progress_cb is not None:
            with suppress(Exception):
                progress_cb(len(found))
    return found


@router.callback_query(F.data.startswith("bd:list:"))
async def bulk_delete_list_cb(
    callback: CallbackQuery, state: FSMContext, db: Database,
    telethon: TelethonService, forwarding: ForwardingEngine,
) -> None:
    if callback.message is None:
        return
    if not await _allowed(db, callback.from_user.id):
        return await callback.answer("Gold and above only", show_alert=True)
    _, _, kind, page_str = callback.data.split(":")
    page = int(page_str)
    language = await _lang(db, callback.from_user.id)
    kind_label = "channel" if kind == "ch" else "group"

    data = await state.get_data()
    chats = data.get(f"bd_chats_{kind}")

    if not chats:
        # The loader starts BEFORE the client is acquired. Connecting a client
        # itself takes a second or two, and starting the animation after it
        # left the screen frozen during exactly the part people notice.
        client = None
        owned = False
        async with _typing(callback.bot, callback.message.chat.id), \
                Loader(callback.message, "load_chats", language) as loader:
            client, owned = await _acquire_client(
                callback.from_user.id, telethon, forwarding,
            )
            if client is not None:
                try:
                    chats = await _collect_chats(
                        client, kind, progress_cb=lambda n: setattr(loader, "done", n),
                    )
                except Exception:
                    logger.exception("Could not list chats for %s", callback.from_user.id)
                    chats = []
                finally:
                    await _release_client(client, owned)

        if client is None:
            return await _show(
                callback.message, safe_t(language, "connect_required"),
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect", style=STYLE_GO)],
                ]),
            )
        await state.update_data({f"bd_chats_{kind}": chats})

    if not chats:
        await _show(
            callback.message,
            safe_t(language, "bd_no_chats", kind=f"{kind_label}s"),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Back", callback_data="bd:intro")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
        )
        return await callback.answer()

    pages = max(1, (len(chats) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = []
    for idx, chat in enumerate(chats[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]):
        real_index = page * PAGE_SIZE + idx
        rows.append([InlineKeyboardButton(
            text=f"📛 {chat['title'][:40]}",
            callback_data=f"bd:pick:{kind}:{real_index}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Back", callback_data=f"bd:list:{kind}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️ Next", callback_data=f"bd:list:{kind}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="menu:home")])

    await _show(
        callback.message,
        safe_t(language, "bd_pick_chat", kind=kind_label.title(), page=page + 1, pages=pages),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "bd:intro")
async def bulk_delete_intro_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    if not await _allowed(db, callback.from_user.id):
        return await callback.answer("Gold and above only", show_alert=True)
    language = await _lang(db, callback.from_user.id)
    await _show(callback.message, safe_t(language, "bd_intro"), _intro_markup())
    await callback.answer()


# ==========================================
# STEP 3/4 — CONFIRMATION
# ==========================================

@router.callback_query(F.data.startswith("bd:pick:"))
async def bulk_delete_pick_cb(
    callback: CallbackQuery, state: FSMContext, db: Database,
) -> None:
    """Chat chosen — now pick what to delete."""
    if callback.message is None:
        return
    if not await _allowed(db, callback.from_user.id):
        return await callback.answer("Gold and above only", show_alert=True)
    _, _, kind, index_str = callback.data.split(":")
    language = await _lang(db, callback.from_user.id)
    data = await state.get_data()
    chats = data.get(f"bd_chats_{kind}") or []
    index = int(index_str)
    if index < 0 or index >= len(chats):
        return await callback.answer("Please open the list again", show_alert=True)

    chat = chats[index]
    await state.update_data(
        bd_chat_id=chat["id"], bd_chat_title=chat["title"], bd_kind=kind,
    )
    rows = [
        [InlineKeyboardButton(text="🗑️ Everything", callback_data="bd:f:all")],
        [InlineKeyboardButton(text="🕐 Last 24 hours", callback_data="bd:f:24h")],
        [InlineKeyboardButton(text="📅 After a date", callback_data="bd:f:after"),
         InlineKeyboardButton(text="📅 Before a date", callback_data="bd:f:before")],
        [InlineKeyboardButton(text="📆 Between two dates", callback_data="bd:f:range")],
        [InlineKeyboardButton(text="🖼️ Only media", callback_data="bd:f:media")],
        [InlineKeyboardButton(text="🔤 Containing a word", callback_data="bd:f:keyword")],
        [InlineKeyboardButton(text="👤 By a specific user", callback_data="bd:f:user")],
        [InlineKeyboardButton(text="🧹 Service messages", callback_data="bd:f:service")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="menu:home")],
    ]
    await _show(
        callback.message,
        safe_t(language, "bd_pick_filter", chat=safe_html(chat["title"])),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


FILTER_LABELS = {
    "all": "All messages",
    "24h": "Messages from the last 24 hours",
    "media": "Only photos, videos and files",
    "service": "Only service messages (joined / left / pinned)",
}


@router.callback_query(F.data.startswith("bd:f:"))
async def bulk_delete_filter_cb(
    callback: CallbackQuery, state: FSMContext, db: Database,
    telethon: TelethonService, forwarding: ForwardingEngine,
) -> None:
    """A filter was chosen. Some need a follow-up value first."""
    if callback.message is None:
        return
    if not await _allowed(db, callback.from_user.id):
        return await callback.answer("Gold and above only", show_alert=True)
    choice = callback.data.rsplit(":", 1)[1]
    language = await _lang(db, callback.from_user.id)
    data = await state.get_data()
    chat_title = str(data.get("bd_chat_title") or "")

    if choice in ("after", "before"):
        await state.set_state(BulkDeleteStates.waiting_date)
        await state.update_data(bd_filter=choice)
        return await _ask(
            callback,
            safe_t(
                language, "bd_date_prompt",
                explain=safe_t(language, f"bd_date_{choice}"),
            ),
        )

    if choice == "range":
        await state.set_state(BulkDeleteStates.waiting_range_start)
        await state.update_data(bd_filter="range")
        return await _ask(callback, safe_t(language, "bd_range_start"))

    if choice == "keyword":
        await state.set_state(BulkDeleteStates.waiting_keyword)
        await state.update_data(bd_filter=choice)
        return await _ask(callback, safe_t(language, "bd_keyword_prompt"))

    if choice == "user":
        return await _bulk_delete_user_step(
            callback, state, db, telethon, forwarding, language, chat_title,
        )

    if choice not in FILTER_LABELS:
        return await callback.answer("Invalid option", show_alert=True)

    await state.update_data(bd_filter=choice, bd_filter_value=None)
    await _bulk_delete_confirm_screen(
        callback.message, state, db, language, FILTER_LABELS[choice],
    )
    await callback.answer()


async def _ask(callback: CallbackQuery, text: str) -> None:
    await _show(
        callback.message, text,
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="bd:cancel")],
        ]),
    )
    await callback.answer()


async def _bulk_delete_user_step(
    callback: CallbackQuery, state: FSMContext, db: Database,
    telethon: TelethonService, forwarding: ForwardingEngine,
    language: str, chat_title: str,
) -> None:
    """Offers an admin list for channels, or asks for a username in groups.

    In a CHANNEL every post belongs to the channel unless "Sign messages" is
    on — so when it is off, filtering by user genuinely cannot work and the
    user is told that instead of being handed a job that finds nothing.
    """
    data = await state.get_data()
    kind = str(data.get("bd_kind") or "gr")
    chat_id = int(data.get("bd_chat_id") or 0)
    await state.update_data(bd_filter="user")

    if kind == "ch":
        client, owned = await _acquire_client(callback.from_user.id, telethon, forwarding)
        if client is None:
            return await _ask(callback, safe_t(language, "bd_user_prompt"))
        try:
            entity = await client.get_entity(chat_id)
            signed = bool(getattr(entity, "signatures", False))
            admins = []
            if signed:
                async for participant in client.iter_participants(entity, filter=None, limit=60):
                    if getattr(participant, "bot", False):
                        continue
                    admins.append(participant)
        except Exception:
            logger.exception("Could not read channel admins")
            return await _ask(callback, safe_t(language, "bd_no_admins"))
        finally:
            await _release_client(client, owned)

        if not signed:
            await _show(
                callback.message,
                safe_t(language, "bd_user_unsigned", chat=safe_html(chat_title)),
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="◀️ Back", callback_data="bd:intro")],
                    [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
                ]),
            )
            return await callback.answer()

        if admins:
            await state.update_data(
                bd_admins=[{"id": a.id,
                            "name": (a.first_name or a.username or str(a.id))}
                           for a in admins[:20]],
            )
            rows = [[InlineKeyboardButton(
                text=f"👤 {(a.first_name or a.username or a.id)}"[:40],
                callback_data=f"bd:u:{i}",
            )] for i, a in enumerate(admins[:20])]
            rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="bd:cancel")])
            await _show(
                callback.message,
                safe_t(language, "bd_pick_admin", chat=safe_html(chat_title)),
                InlineKeyboardMarkup(inline_keyboard=rows),
            )
            return await callback.answer()

    await state.set_state(BulkDeleteStates.waiting_user)
    await _ask(callback, safe_t(language, "bd_user_prompt"))


@router.callback_query(F.data.startswith("bd:u:"))
async def bulk_delete_admin_pick_cb(
    callback: CallbackQuery, state: FSMContext, db: Database,
) -> None:
    if callback.message is None:
        return
    if not await _allowed(db, callback.from_user.id):
        return await callback.answer("Gold and above only", show_alert=True)
    index = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    admins = data.get("bd_admins") or []
    if index < 0 or index >= len(admins):
        return await callback.answer("Please start again", show_alert=True)
    chosen = admins[index]
    language = await _lang(db, callback.from_user.id)
    await state.update_data(bd_filter="user", bd_filter_value=chosen["id"])
    await _bulk_delete_confirm_screen(
        callback.message, state, db, language,
        f"Only posts by {chosen['name']}",
    )
    await callback.answer()


@router.message(BulkDeleteStates.waiting_date)
async def bulk_delete_date_input(
    message: Message, state: FSMContext, db: Database,
) -> None:
    language = await _lang(db, message.from_user.id)
    raw = (message.text or "").strip()
    if raw == "/back":
        await state.clear()
        return await message.answer(safe_t(language, "bd_cancelled"), parse_mode="HTML")

    parsed = parse_short_date(raw)
    if parsed is None:
        return await message.answer(safe_t(language, "bd_date_bad"), parse_mode="HTML")
    if parsed > datetime.now(timezone.utc):
        return await message.answer(safe_t(language, "bd_date_future"), parse_mode="HTML")

    data = await state.get_data()
    which = str(data.get("bd_filter") or "after")
    await state.update_data(bd_filter_value=parsed.isoformat())
    # The PARSED date is echoed back: this is irreversible, and one typo
    # could mean a completely different range.
    pretty = parsed.strftime("%d %b %Y")
    label = (
        f"Posts after {pretty}" if which == "after" else f"Posts before {pretty}"
    )
    await _bulk_delete_confirm_screen(message, state, db, language, label)


@router.message(BulkDeleteStates.waiting_range_start)
async def bulk_delete_range_start(
    message: Message, state: FSMContext, db: Database,
) -> None:
    """Step 1 of 2 — the start of the range."""
    language = await _lang(db, message.from_user.id)
    raw = (message.text or "").strip()
    if raw == "/back":
        await state.clear()
        return await message.answer(safe_t(language, "bd_cancelled"), parse_mode="HTML")

    parsed = parse_short_date(raw)
    if parsed is None:
        return await message.answer(safe_t(language, "bd_date_bad"), parse_mode="HTML")
    if parsed > datetime.now(timezone.utc):
        return await message.answer(safe_t(language, "bd_date_future"), parse_mode="HTML")

    await state.update_data(bd_range_start=parsed.isoformat())
    await state.set_state(BulkDeleteStates.waiting_range_end)
    await message.answer(
        safe_t(language, "bd_range_end", start=parsed.strftime("%d %b %Y")),
        parse_mode="HTML",
    )


@router.message(BulkDeleteStates.waiting_range_end)
async def bulk_delete_range_end(
    message: Message, state: FSMContext, db: Database,
) -> None:
    """Step 2 of 2 — the end of the range, then confirm."""
    language = await _lang(db, message.from_user.id)
    raw = (message.text or "").strip()
    if raw == "/back":
        await state.clear()
        return await message.answer(safe_t(language, "bd_cancelled"), parse_mode="HTML")

    parsed = parse_short_date(raw)
    if parsed is None:
        return await message.answer(safe_t(language, "bd_date_bad"), parse_mode="HTML")

    data = await state.get_data()
    start = datetime.fromisoformat(str(data.get("bd_range_start")))
    # The END date is inclusive, so the whole of that day counts.
    end = parsed + timedelta(days=1)
    if end <= start:
        return await message.answer(
            safe_t(
                language, "bd_range_bad_order",
                start=start.strftime("%d %b %Y"), end=parsed.strftime("%d %b %Y"),
            ),
            parse_mode="HTML",
        )

    await state.update_data(bd_filter_value=f"{start.isoformat()}|{end.isoformat()}")
    await _bulk_delete_confirm_screen(
        message, state, db, language,
        f"Posts between {start.strftime('%d %b %Y')} and {parsed.strftime('%d %b %Y')}",
    )


@router.message(BulkDeleteStates.waiting_keyword)
async def bulk_delete_keyword_input(
    message: Message, state: FSMContext, db: Database,
) -> None:
    language = await _lang(db, message.from_user.id)
    raw = (message.text or "").strip()
    if raw == "/back":
        await state.clear()
        return await message.answer(safe_t(language, "bd_cancelled"), parse_mode="HTML")
    if not 1 <= len(raw) <= 100:
        return await message.answer("⚠️ Please send between 1 and 100 characters.")
    await state.update_data(bd_filter_value=raw)
    await _bulk_delete_confirm_screen(
        message, state, db, language, f'Posts containing "{raw}"',
    )


@router.message(BulkDeleteStates.waiting_user)
async def bulk_delete_user_input(
    message: Message, state: FSMContext, db: Database,
) -> None:
    language = await _lang(db, message.from_user.id)
    raw = (message.text or "").strip()
    if raw == "/back":
        await state.clear()
        return await message.answer(safe_t(language, "bd_cancelled"), parse_mode="HTML")
    value = raw.lstrip("@")
    if not value:
        return await message.answer(safe_t(language, "bd_user_prompt"), parse_mode="HTML")
    await state.update_data(bd_filter_value=int(value) if value.isdigit() else value)
    await _bulk_delete_confirm_screen(
        message, state, db, language, f"Only posts by {raw}",
    )


async def _bulk_delete_confirm_screen(
    message_obj, state: FSMContext, db: Database, language: str, scope_label: str,
) -> None:
    data = await state.get_data()
    chat_title = str(data.get("bd_chat_title") or "")
    await state.set_state(BulkDeleteStates.waiting_confirm)
    await state.update_data(bd_asked_at=time.time(), bd_scope_label=scope_label)
    await _show(
        message_obj,
        safe_t(
            language, "bd_confirm",
            chat=safe_html(chat_title), scope=safe_html(scope_label),
        ),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="bd:cancel")],
        ]),
    )


@router.callback_query(F.data == "bd:cancel")
async def bulk_delete_cancel_cb(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.message is None:
        return
    await state.clear()
    language = await _lang(db, callback.from_user.id)
    await _show(
        callback.message, safe_t(language, "bd_cancelled"),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "bd:stop")
async def bulk_delete_stop_cb(callback: CallbackQuery, db: Database) -> None:
    if not await _allowed(db, callback.from_user.id):
        return await callback.answer("Gold and above only", show_alert=True)
    job = _JOBS.get(callback.from_user.id)
    if not job:
        return await callback.answer("Nothing is running", show_alert=True)
    job["cancel"] = True
    await callback.answer("Stopping… finishing the current batch", show_alert=True)


# ==========================================
# STEP 5/6/7 — THE JOB
# ==========================================

@router.message(BulkDeleteStates.waiting_confirm)
async def bulk_delete_confirm(
    message: Message, state: FSMContext, db: Database, settings: Settings,
    telethon: TelethonService, forwarding: ForwardingEngine,
) -> None:
    language = await _lang(db, message.from_user.id)
    data = await state.get_data()
    chat_id = data.get("bd_chat_id")
    chat_title = str(data.get("bd_chat_title") or chat_id)
    asked_at = float(data.get("bd_asked_at") or 0)

    if chat_id is None:
        await state.clear()
        return

    if time.time() - asked_at > CONFIRM_TIMEOUT:
        await state.clear()
        return await message.answer(safe_t(language, "bd_expired"), parse_mode="HTML")

    # Exact word, capitals, nothing else. Anything at all besides DELETE
    # cancels — for an irreversible action, "close enough" is not enough.
    if (message.text or "").strip() != CONFIRM_WORD:
        await state.clear()
        return await message.answer(safe_t(language, "bd_cancelled"), parse_mode="HTML")

    await state.clear()

    if _JOBS.get(message.from_user.id):
        return await message.answer(
            safe_t(language, "bd_busy",
                   deleted=f"{_JOBS[message.from_user.id].get('deleted', 0):,}"),
            parse_mode="HTML",
        )
    if forwarding.transfer_state(message.from_user.id):
        return await message.answer(safe_t(language, "bd_transfer_busy"), parse_mode="HTML")

    status = await message.answer(
        safe_t(language, "bd_scanning", deleted="0", chat=safe_html(chat_title)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏹️ Stop", callback_data="bd:stop")],
        ]),
        parse_mode="HTML",
    )

    await _run_delete_job(
        message.bot, db, settings, telethon, forwarding,
        message.from_user.id, int(chat_id), chat_title, language, status,
        str(data.get("bd_filter") or "all"), data.get("bd_filter_value"),
        str(data.get("bd_scope_label") or "All messages"),
    )


async def _run_delete_job(
    bot: Bot, db: Database, settings: Settings, telethon: TelethonService,
    forwarding: ForwardingEngine, user_id: int, chat_id: int, chat_title: str,
    language: str, status: Message,
    filter_kind: str = "all", filter_value=None, scope_label: str = "All messages",
) -> None:
    job = {"cancel": False, "deleted": 0, "failed": 0, "chat": chat_title,
           "scope": scope_label}
    _JOBS[user_id] = job
    started = time.time()
    client, owned = None, False
    reason = ""
    total_hint = 0

    try:
        client, owned = await _acquire_client(user_id, telethon, forwarding)
        if client is None:
            await _show(status, safe_t(language, "connect_required"), None)
            return

        entity = await client.get_entity(chat_id)

        # Re-check the right immediately before deleting: it may have been
        # taken away between listing the chat and confirming.
        if not await _can_delete(client, entity):
            await _show(
                status,
                safe_t(language, "bd_no_permission", chat=safe_html(chat_title)),
                None,
            )
            return

        with_total = await client.get_messages(entity, limit=1)
        total_hint = int(getattr(with_total, "total", 0) or 0)

        # Server-side narrowing wherever Telegram supports it — iterating the
        # whole history and discarding most of it would be far slower.
        iter_kwargs: dict = {}
        range_start = range_end = None
        if filter_kind == "24h":
            iter_kwargs["offset_date"] = datetime.now(timezone.utc) - timedelta(hours=24)
            iter_kwargs["reverse"] = True
        elif filter_kind == "after" and filter_value:
            iter_kwargs["offset_date"] = datetime.fromisoformat(str(filter_value))
            iter_kwargs["reverse"] = True
        elif filter_kind == "before" and filter_value:
            # offset_date walks BACKWARDS from this point, which is exactly
            # "everything older than this".
            iter_kwargs["offset_date"] = datetime.fromisoformat(str(filter_value))
        elif filter_kind == "range" and filter_value:
            start_raw, end_raw = str(filter_value).split("|")
            range_start = datetime.fromisoformat(start_raw)
            range_end = datetime.fromisoformat(end_raw)
            iter_kwargs["offset_date"] = range_start
            iter_kwargs["reverse"] = True
        elif filter_kind == "keyword" and filter_value:
            iter_kwargs["search"] = str(filter_value)
        elif filter_kind == "user" and filter_value:
            iter_kwargs["from_user"] = filter_value
        elif filter_kind == "media":
            from telethon.tl.types import InputMessagesFilterPhotoVideoDocuments
            iter_kwargs["filter"] = InputMessagesFilterPhotoVideoDocuments()

        def keep(msg) -> bool:
            """The last word on whether a message matches the filter."""
            if filter_kind == "range":
                when = getattr(msg, "date", None)
                if when is None or not (range_start <= when < range_end):
                    return False
                return getattr(msg, "action", None) is None
            if filter_kind == "service":
                return getattr(msg, "action", None) is not None
            if getattr(msg, "action", None) is not None:
                # Service messages are only removed when explicitly asked for.
                return False
            if filter_kind == "media":
                return getattr(msg, "media", None) is not None
            if filter_kind == "keyword" and filter_value:
                return str(filter_value).lower() in (msg.message or "").lower()
            return True

        delay = BATCH_DELAY
        batch: list[int] = []
        batches_done = 0

        async def flush() -> None:
            """Deletes one batch, retrying the SAME batch after a FloodWait."""
            nonlocal delay, batches_done
            if not batch:
                return
            while True:
                try:
                    await client.delete_messages(entity, batch)
                    job["deleted"] += len(batch)
                    break
                except errors.FloodWaitError as fw:
                    wait = int(getattr(fw, "seconds", 0) or 0)
                    logger.warning("Bulk delete FloodWait %ss for user %s", wait, user_id)
                    # Never skip the batch — those messages would be left behind
                    # and the final count would be a lie.
                    await asyncio.sleep(wait + 2)
                    delay = SLOW_DELAY
                except (errors.ChatAdminRequiredError, errors.ChannelPrivateError) as exc:
                    raise exc
                except Exception as exc:
                    logger.info("Bulk delete batch refused for %s: %s", user_id, exc)
                    job["failed"] += len(batch)
                    break
            batch.clear()
            batches_done += 1
            await asyncio.sleep(delay)

        # Streamed, not collected. Holding every message id of a large channel
        # in memory would be a real cost on a small host, and streaming also
        # starts deleting immediately instead of after a full scan.
        async for msg in client.iter_messages(entity, **iter_kwargs):
            if job["cancel"]:
                break
            if not keep(msg):
                continue
            batch.append(msg.id)
            if len(batch) >= DELETE_BATCH:
                await flush()
                if batches_done % 5 == 0:
                    await _update_progress(status, language, job, total_hint, chat_title)
        if not job["cancel"]:
            await flush()

    except errors.ChatAdminRequiredError:
        reason = "You are not an admin with delete rights in that chat."
    except errors.ChannelPrivateError:
        reason = "The chat is no longer reachable from your account."
    except errors.FloodWaitError as fw:
        reason = f"Telegram asked to wait {int(getattr(fw, 'seconds', 0) or 0)}s."
    except Exception as exc:
        logger.exception("Bulk delete failed for user %s", user_id)
        reason = str(exc)[:180]
    finally:
        await _release_client(client, owned)
        _JOBS.pop(user_id, None)

    took = _took(time.time() - started)
    deleted = f"{job['deleted']:,}"

    if job["cancel"]:
        text = safe_t(language, "bd_stopped", chat=safe_html(chat_title), deleted=deleted)
    elif reason or job["failed"]:
        text = safe_t(
            language, "bd_partial", chat=safe_html(chat_title), deleted=deleted,
            failed=f"{job['failed']:,}", reason=safe_html(reason or "Telegram refused them."),
        )
    else:
        text = safe_t(language, "bd_done", chat=safe_html(chat_title),
                      scope=safe_html(scope_label), deleted=deleted, took=took)

    await _show(
        status, text,
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑️ Delete Another", callback_data="bd:intro")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await _log_to_admins(
        bot, db, settings, user_id, chat_id, chat_title, job, took, reason,
    )


async def _update_progress(
    status: Message, language: str, job: dict, total: int, chat_title: str,
) -> None:
    """Edited at most every 5 batches — Telegram rate-limits message edits."""
    stop = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹️ Stop", callback_data="bd:stop")],
    ])
    deleted = job["deleted"]
    try:
        if total > 0:
            pct = min(100, int(100 * deleted / total))
            text = safe_t(
                language, "bd_running", bar=_bar(deleted, total), percent=pct,
                deleted=f"{deleted:,}", chat=safe_html(chat_title),
            )
        else:
            text = safe_t(
                language, "bd_scanning",
                deleted=f"{deleted:,}", chat=safe_html(chat_title),
            )
        await status.edit_text(text, reply_markup=stop, parse_mode="HTML")
    except TelegramBadRequest:
        pass
    except Exception as exc:
        logger.debug("Could not update delete progress: %s", exc)


async def _log_to_admins(
    bot: Bot, db: Database, settings: Settings, user_id: int, chat_id: int,
    chat_title: str, job: dict, took: str, reason: str,
) -> None:
    """Every job is reported, successful or not — this is the one irreversible
    action in the bot, so there is always a record of who ran what."""
    user = await db.get_user(user_id)
    name = safe_html(user["first_name"] or user["username"] or user_id) if user else str(user_id)
    handle = f"@{safe_html(user['username'])}" if user and user["username"] else "no username"
    plan_raw = str(user["plan"]) if user else "free"
    status_line = (
        "⏹️ Stopped by user" if job["cancel"]
        else ("⚠️ Partial" if (reason or job["failed"]) else "✅ Completed")
    )
    text = (
        f"🗑️ <b>Bulk Delete Job</b>\n\n"
        f"👤 {name} ({handle})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"{plan_label(plan_raw)}\n\n"
        f"📛 Chat: <b>{safe_html(chat_title)}</b>\n"
        f"🆔 Chat ID: <code>{chat_id}</code>\n"
        f"🗑️ Filter: <b>{safe_html(job.get('scope') or 'All messages')}</b>\n\n"
        f"🗑️ Deleted: <b>{job['deleted']:,}</b>\n"
        f"❌ Refused: {job['failed']:,}\n"
        f"⏱️ Took: {took}\n"
        f"{status_line}"
    )
    if reason:
        text += f"\n📝 {safe_html(reason)}"
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.debug("Could not report delete job to admin %s: %s", admin_id, exc)
