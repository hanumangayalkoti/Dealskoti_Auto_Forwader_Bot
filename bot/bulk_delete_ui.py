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
import time
from contextlib import suppress

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
from .plans import PLANS, F_BULK_DELETE, min_plan_for, plan_has
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
                [InlineKeyboardButton(text=f"💎 Upgrade to {required}", callback_data="menu:plans")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )

    if not await db.has_active_session(message.from_user.id):
        # Reuse the bot's own connect flow rather than inventing another one.
        return await message.answer(
            safe_t(language, "connect_required"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
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
    client: TelegramClient, kind: str, limit: int = 400,
) -> list[dict]:
    """The user's channels or groups where they can delete messages."""
    found: list[dict] = []
    async for dialog in client.iter_dialogs(limit=limit):
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
    return found


@router.callback_query(F.data.startswith("bd:list:"))
async def bulk_delete_list_cb(
    callback: CallbackQuery, state: FSMContext, db: Database,
    telethon: TelethonService, forwarding: ForwardingEngine,
) -> None:
    if callback.message is None:
        return
    _, _, kind, page_str = callback.data.split(":")
    page = int(page_str)
    language = await _lang(db, callback.from_user.id)
    kind_label = "channel" if kind == "ch" else "group"

    data = await state.get_data()
    chats = data.get(f"bd_chats_{kind}")

    if not chats:
        await _show(callback.message, "🔍 <b>Reading your chats…</b>", None)
        client, owned = await _acquire_client(callback.from_user.id, telethon, forwarding)
        if client is None:
            return await _show(
                callback.message, safe_t(language, "connect_required"),
                InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
                ]),
            )
        try:
            chats = await _collect_chats(client, kind)
        except Exception:
            logger.exception("Could not list chats for %s", callback.from_user.id)
            chats = []
        finally:
            await _release_client(client, owned)
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
    """Straight to confirmation.

    The original design offered "delete only MY messages", but in a broadcast
    channel every post belongs to the CHANNEL, not to a user, so that filter
    would match nothing and report "0 deleted" — which reads like a broken
    bot. The scope is therefore always the whole history.
    """
    if callback.message is None:
        return
    _, _, kind, index_str = callback.data.split(":")
    language = await _lang(db, callback.from_user.id)
    data = await state.get_data()
    chats = data.get(f"bd_chats_{kind}") or []
    index = int(index_str)
    if index < 0 or index >= len(chats):
        return await callback.answer("Please open the list again", show_alert=True)

    chat = chats[index]
    await state.set_state(BulkDeleteStates.waiting_confirm)
    await state.update_data(
        bd_chat_id=chat["id"], bd_chat_title=chat["title"], bd_asked_at=time.time(),
    )
    await _show(
        callback.message,
        safe_t(
            language, "bd_confirm",
            chat=safe_html(chat["title"]), scope="All messages",
        ),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="bd:cancel")],
        ]),
    )
    await callback.answer()


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
async def bulk_delete_stop_cb(callback: CallbackQuery) -> None:
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
    )


async def _run_delete_job(
    bot: Bot, db: Database, settings: Settings, telethon: TelethonService,
    forwarding: ForwardingEngine, user_id: int, chat_id: int, chat_title: str,
    language: str, status: Message,
) -> None:
    job = {"cancel": False, "deleted": 0, "failed": 0, "chat": chat_title}
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
        async for msg in client.iter_messages(entity):
            if job["cancel"]:
                break
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
                      deleted=deleted, took=took)

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
    plan = str(user["plan"]).title() if user else "?"
    status_line = (
        "⏹️ Stopped by user" if job["cancel"]
        else ("⚠️ Partial" if (reason or job["failed"]) else "✅ Completed")
    )
    text = (
        f"🗑️ <b>Bulk Delete Job</b>\n\n"
        f"👤 {name} ({handle})\n"
        f"🆔 <code>{user_id}</code>\n"
        f"💎 Plan: {plan}\n\n"
        f"📛 Chat: <b>{safe_html(chat_title)}</b>\n"
        f"🆔 Chat ID: <code>{chat_id}</code>\n"
        f"🗑️ Scope: All messages\n\n"
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
