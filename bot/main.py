"""
DealsKoti Auto Forwarder — bot entrypoint.

This file owns: onboarding, the channel gate, login, tasks, the chat picker,
file upload, admin tools, the Razorpay webhook and the process bootstrap.

Two large areas live in their own modules and are wired in as routers:
  * settings_ui.py  — the whole /settings tree (27 per-task features)
  * billing_ui.py   — plans, Razorpay / USDT / Stars, admin payment review

Router order matters. settings_ui and billing_ui are included FIRST so their
callbacks are matched before this module's catch-all message fallback.

NON-NEGOTIABLE RULES (do not "optimise" these away in a rewrite):
  1. Never re-add settings or billing handlers here. Duplicated handlers for
     the same callback silently shadow each other and are miserable to debug.
  2. The 2FA password message is deleted the instant it arrives, before it is
     validated. It must never sit in the chat history.
  3. Feature access is decided by plans.plan_has(), never by comparing plan
     names.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    ErrorEvent,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from .billing import BillingError, RazorpayBilling
from .billing_ui import router as billing_router
from .config import ConfigurationError, Settings
from .db import Database
from .faq import FAQS
from .forwarding import ForwardingEngine
from .gate import enforce_gate, user_is_member
from .locales import (
    ADMIN_COMMANDS,
    USER_COMMANDS,
    admin_help,
    command_help,
    language_for,
    t,
)
from .plans import MIN_WITHDRAWAL_PAISE, PLANS, REFERRAL_RATE, duration_days, format_paise
from .settings_ui import router as settings_router
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti")
router = Router(name="dealskoti")

IST = ZoneInfo("Asia/Kolkata")


# ==========================================
# FSM STATES
# ==========================================

class LoginStates(StatesGroup):
    waiting_phone = State()
    waiting_pin = State()
    waiting_2fa = State()


class TaskStates(StatesGroup):
    waiting_name = State()
    waiting_source = State()
    waiting_destination = State()


class UploadStates(StatesGroup):
    waiting_file = State()


class AdminStates(StatesGroup):
    waiting_grant_days = State()


class AdminGrantStates(StatesGroup):
    waiting_custom_days = State()


class PayoutStates(StatesGroup):
    waiting_address = State()


class AdminBroadcastStates(StatesGroup):
    waiting_message = State()


# ==========================================
# SMALL HELPERS
# ==========================================

@asynccontextmanager
async def _busy(bot: Bot, chat_id: int):
    """Shows Telegram's native "typing…" indicator while a slow job runs.

    Used only around genuinely slow work (Telegram round-trips: reading the
    dialog list, validating a chat, uploading a file). Telegram clears the
    indicator after ~5s, so it is refreshed on a loop until the job finishes.
    """
    async def _loop():
        try:
            while True:
                with suppress(Exception):
                    await bot.send_chat_action(chat_id, "typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def _is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids


def safe_html(text) -> str:
    """Escapes text for HTML parse mode so user content can never break a screen."""
    return html.escape(str(text))


def safe_t(lang: str, key: str, **kwargs) -> str:
    try:
        return t(lang, key, **kwargs)
    except Exception:
        logger.warning("Missing translation key %r for language %r", key, lang)
        return f"[{key}]"


def _json_field(value, default):
    """asyncpg returns JSONB as a parsed object or a raw string depending on
    codec setup, so every read has to handle both."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value or ("[]" if isinstance(default, list) else "{}"))
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


async def _ensure_user(db: Database, message: Message):
    return await db.ensure_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name,
    )


async def _language_for_message(db: Database, message: Message) -> str:
    user = await _ensure_user(db, message)
    return language_for(user["preferred_language"]) if user else "en"


async def _language_for_callback(db: Database, callback: CallbackQuery) -> str:
    user = await db.ensure_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name,
    )
    return language_for(user["preferred_language"]) if user else "en"


async def _notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    for admin_id in settings.admin_telegram_ids:
        with suppress(Exception):
            await bot.send_message(admin_id, text, parse_mode="HTML")


async def _require_connected(db: Database, user_id: int, language: str) -> str | None:
    """Returns the 'connect first' message when the user has no session."""
    if await db.has_active_session(user_id):
        return None
    return safe_t(language, "connect_required")


def _connect_required_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])


def _nav_keyboard(*, back: str = "menu:home", include_cancel: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="◀️ Back", callback_data=back)]]
    if include_cancel:
        rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _safe_edit(message_obj, text: str, reply_markup=None) -> None:
    """edit_text that ignores the harmless 'message is not modified' error."""
    try:
        await message_obj.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


def _format_name(user) -> str:
    if user is None:
        return "User"
    return safe_html(user["first_name"] or user["username"] or user["telegram_user_id"])


async def _menu_text(db: Database, user_id: int, language: str) -> str:
    user = await db.get_user(user_id)
    name = safe_html(user["first_name"] or user["username"] or "User") if user else "User"
    return safe_t(language, "main_menu", name=name)


def main_menu_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect"),
         InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks")],
        [InlineKeyboardButton(text="➕ New Task", callback_data="task:create"),
         InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings")],
        [InlineKeyboardButton(text="💎 Plans", callback_data="menu:plans"),
         InlineKeyboardButton(text="👤 My Account", callback_data="menu:account")],
        [InlineKeyboardButton(text="❓ FAQ", callback_data="faq:page:0"),
         InlineKeyboardButton(text="🎁 Refer & Earn", callback_data="menu:refer")],
    ]
    if settings.support_bot_link:
        rows.append([InlineKeyboardButton(text="📞 Support", url=settings.support_bot_link)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="language:en")],
        [InlineKeyboardButton(text="🇮🇳 Hinglish", callback_data="language:hinglish")],
    ])


# ==========================================
# FLOW INTERRUPTION
# ==========================================
# A user halfway through /connect who suddenly sends /plans used to get the
# plans screen while the bot silently stayed in "waiting for phone number"
# state — their next ordinary message was then read as a phone number and
# rejected, with no explanation anywhere. This middleware cancels the pending
# flow FIRST and says what was cancelled, so nothing is ever ignored silently.
#
# Living in one middleware means every flow is covered — including the ones in
# settings_ui and billing_ui — without touching a single handler.

# Commands that are PART of a flow rather than an escape from it.
FLOW_INTERNAL_COMMANDS = {"back", "done", "clear", "cancel", "skip"}

FLOW_LABELS: dict[str, str] = {
    "LoginStates:waiting_phone": "Connect Account",
    "LoginStates:waiting_pin": "Connect Account",
    "LoginStates:waiting_2fa": "Connect Account",
    "TaskStates:waiting_name": "New Task",
    "TaskStates:waiting_source": "Choosing Sources",
    "TaskStates:waiting_destination": "Choosing Destinations",
    "UploadStates:waiting_file": "File Upload",
    "SettingsFlow:waiting_value": "Editing a Setting",
    "ManualPayStates:waiting_proof": "Payment Proof",
    "AdminStates:waiting_grant_days": "Grant Days",
    "AdminGrantStates:waiting_custom_days": "Grant Days",
    "PayoutStates:waiting_address": "Payment Method",
    "AdminBroadcastStates:waiting_message": "Broadcast",
}

FLOW_STEP_HINTS: dict[str, str] = {
    "LoginStates:waiting_phone": "You were entering your phone number.",
    "LoginStates:waiting_pin": "You were entering your login PIN.",
    "LoginStates:waiting_2fa": "You were entering your 2FA password.",
    "TaskStates:waiting_name": "You were naming a new task.",
    "TaskStates:waiting_source": "You were selecting source channels.",
    "TaskStates:waiting_destination": "You were selecting destination channels.",
    "UploadStates:waiting_file": "You were uploading a file.",
    "SettingsFlow:waiting_value": "You were changing a setting.",
    "ManualPayStates:waiting_proof": "You were submitting payment proof.",
    "AdminBroadcastStates:waiting_message": "You were writing a broadcast.",
    "PayoutStates:waiting_address": "You were setting your payout address.",
}


class FlowInterruptMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state: FSMContext | None = data.get("state")
        if state is None or not isinstance(event, Message) or not event.text:
            return await handler(event, data)

        text = event.text.strip()
        if not text.startswith("/"):
            return await handler(event, data)

        command = text.split()[0].lstrip("/").split("@")[0].lower()
        if command in FLOW_INTERNAL_COMMANDS:
            return await handler(event, data)

        current = await state.get_state()
        if not current:
            return await handler(event, data)

        label = FLOW_LABELS.get(current)
        if label is None:
            return await handler(event, data)

        # A pending Telethon login holds an open client; drop it too, not just
        # the FSM state, or the half-finished login lingers in memory.
        if current.startswith("LoginStates:"):
            telethon = data.get("telethon")
            if telethon is not None:
                with suppress(Exception):
                    await telethon.cancel_login(event.from_user.id)

        await state.clear()

        db = data.get("db")
        language = "en"
        if db is not None:
            with suppress(Exception):
                user = await db.get_user(event.from_user.id)
                if user is not None:
                    language = language_for(user["preferred_language"])

        hint = FLOW_STEP_HINTS.get(current, "")
        with suppress(Exception):
            await event.answer(
                safe_t(language, "flow_cancelled", flow=safe_html(label), hint=safe_html(hint)),
                parse_mode="HTML",
            )
        return await handler(event, data)


# ==========================================
# GLOBAL ERROR HANDLER
# ==========================================

@router.errors()
async def global_error_handler(event: ErrorEvent, settings: Settings) -> bool:
    exc = event.exception
    # Telegram routinely drops the HTTP connection after a callback has already
    # been actioned. Reporting those would bury the errors that actually matter.
    if isinstance(exc, TelegramBadRequest) and "query is too old" in str(exc):
        return True
    if isinstance(exc, TelegramForbiddenError):
        return True
    logger.exception("Unhandled error while processing update", exc_info=exc)
    return True


# ==========================================
# START / MENU / HELP
# ==========================================
# ==========================================
# HOME SCREEN (state-aware)
# ==========================================
# Three different screens depending on where the user actually is. Showing a
# Connect button to someone already connected, or task buttons to someone who
# cannot use them yet, is how a new user gets lost on their first screen.

def _ago(when) -> str:
    """Human-friendly 'last forward' age. This one line tells a user whether
    their bot is alive, so it is worth getting right."""
    if when is None:
        return "never"
    delta = datetime.now(timezone.utc) - when
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _who(user) -> str:
    """Username when they have one, otherwise just their first name — never
    the phone number, which does not belong on a screen they might screenshot."""
    if user is None:
        return "You"
    username = user["username"] if "username" in user.keys() else None
    if username:
        return f"@{safe_html(username)}"
    return safe_html(user["first_name"] or "You")


def _days_left(user) -> str:
    if user is None or not user["plan_expiry"]:
        return ""
    remaining = user["plan_expiry"] - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        return " · expired"
    # Round UP: with 22 days and 23 hours left, .days gives 22 and the user
    # feels short-changed. Anything part-way into a day counts as that day.
    days = -(-int(remaining.total_seconds()) // 86400)
    if days <= 1:
        return " · expires today"
    return f" · {days} days left"


async def _home_screen(db: Database, user_id: int, language: str, settings: Settings):
    """Returns (text, keyboard) for /start and /menu."""
    user = await db.get_user(user_id)
    connected = await db.has_active_session(user_id)

    if not connected:
        text = safe_t(language, "home_not_connected")
        rows = [
            [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
            [InlineKeyboardButton(text="💎 View Plans", callback_data="menu:plans")],
            [InlineKeyboardButton(text="❓ How it works", callback_data="faq:page:0")],
        ]
        if settings.support_bot_link:
            rows.append([InlineKeyboardButton(text="📞 Support", url=settings.support_bot_link)])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    tasks = await db.list_tasks(user_id)
    plan_line = f"{plan.name}{_days_left(user)}" if plan.monthly_rupees else f"{plan.name} · {plan.daily_messages} msgs/day"

    if not tasks:
        text = safe_t(
            language, "home_no_tasks", who=_who(user), plan=plan_line,
        )
        rows = [
            [InlineKeyboardButton(text="➕ Create First Task", callback_data="task:create")],
            [InlineKeyboardButton(text="💎 Plans", callback_data="menu:plans"),
             InlineKeyboardButton(text="👤 Account", callback_data="menu:account")],
            [InlineKeyboardButton(text="❓ Help", callback_data="faq:page:0")],
        ]
        if settings.support_bot_link:
            rows.append([InlineKeyboardButton(text="📞 Support", url=settings.support_bot_link)])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    active = sum(1 for t_ in tasks if not t_["is_paused"])
    paused = len(tasks) - active
    stats = await db.usage_stats(user_id)
    cap = plan.daily_messages if plan.daily_messages else "∞"

    task_line = f"{active} active"
    if paused:
        task_line += f" · {paused} paused"

    text = safe_t(
        language, "home_ready",
        who=_who(user), plan=plan_line, tasks=task_line,
        today=stats["today"], cap=cap, last=_ago(stats["last_forward_at"]),
    )
    rows = [
        [InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks"),
         InlineKeyboardButton(text="📊 Stats", callback_data="menu:stats")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
         InlineKeyboardButton(text="🛠️ Config", callback_data="cfg:list")],
        [InlineKeyboardButton(text="💎 Plans", callback_data="menu:plans"),
         InlineKeyboardButton(text="🎁 Refer & Earn", callback_data="menu:refer")],
        [InlineKeyboardButton(text="👤 Account", callback_data="menu:account"),
         InlineKeyboardButton(text="❓ Help", callback_data="faq:page:0")],
    ]
    if settings.support_bot_link:
        rows.append([InlineKeyboardButton(text="📞 Support", url=settings.support_bot_link)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================================
# STATS
# ==========================================

async def _stats_screen(db: Database, user_id: int, language: str):
    user = await db.get_user(user_id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    stats = await db.usage_stats(user_id)
    tasks = await db.list_tasks(user_id)

    cap = plan.daily_messages
    limit_line = f"{stats['today']} / {cap} used" if cap else f"{stats['today']} used · unlimited"

    lines = [
        "📊 <b>Your Forwarding Stats</b>",
        "",
        f"📅 Today: <b>{stats['today']:,}</b> messages",
        f"📆 This month: <b>{stats['month']:,}</b> messages",
        f"🏆 All time: <b>{stats['total']:,}</b> messages",
        "",
        f"⚡ Daily limit: {limit_line}",
        f"🕐 Last forward: <b>{_ago(stats['last_forward_at'])}</b>",
    ]

    if tasks:
        lines.append("")
        lines.append("━━━━━━━━━━━━━━")
        lines.append("📋 <b>Per Task</b>")
        lines.append("")
        for t_ in tasks:
            icon = "⏸️" if t_["is_paused"] else "▶️"
            count = int(t_["forward_count"] or 0)
            last = _ago(t_["last_forward_at"])
            lines.append(f"{icon} <b>{safe_html(t_['task_name'])}</b>")
            lines.append(f"    {count:,} forwarded · last {last}")
        # A task that has never fired is almost always a misconfiguration, so
        # point at it rather than leaving the user to work it out.
        idle = [t_ for t_ in tasks if not t_["is_paused"] and not t_["last_forward_at"]]
        if idle:
            lines.append("")
            lines.append("⚠️ Some tasks have never forwarded anything.")
            lines.append("Check the source channel is correct in /settings.")

    rows = [
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="menu:stats")],
        [InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks"),
         InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("stats2", "mystats"))
async def stats_user_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    text, markup = await _stats_screen(db, message.from_user.id, language)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu:stats")
async def menu_stats_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    text, markup = await _stats_screen(db, callback.from_user.id, language)
    await _safe_edit(callback.message, text, markup)
    await callback.answer()



@router.message(Command("start"))
async def start(message: Message, command: CommandObject, db: Database, settings: Settings) -> None:
    user, is_new = await db.ensure_user_with_status(
        message.from_user.id, message.from_user.username, message.from_user.first_name,
    )

    # Referral: /start ref_<id>
    payload = (command.args or "").strip()
    if is_new and payload.startswith("ref_"):
        raw = payload[4:].strip()
        referrer_id: int | None = None
        if raw.isdigit():
            # Legacy links that carried the raw user id are still honoured, so
            # anything already shared keeps working.
            referrer_id = int(raw)
        elif raw:
            with suppress(Exception):
                referrer_id = await db.user_by_referral_code(raw)
        if referrer_id:
            with suppress(Exception):
                await db.create_referral(referrer_id, message.from_user.id)

    if is_new:
        with suppress(Exception):
            if await db.mark_new_user_notified(message.from_user.id):
                await _notify_admins(
                    message.bot, settings,
                    f"🆕 <b>New user</b>\n"
                    f"Name: {_format_name(user)}\n"
                    f"Username: @{safe_html(message.from_user.username or '—')}\n"
                    f"ID: <code>{message.from_user.id}</code>",
                )

    if not user["language_selected"]:
        await message.answer(safe_t("en", "choose_language"), reply_markup=language_keyboard())
        return

    language = language_for(user["preferred_language"])
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    text, markup = await _home_screen(db, message.from_user.id, language, settings)
    if is_new:
        text = safe_t(language, "home_first_time") + "\n\n" + text
    await message.answer(text, reply_markup=markup)


@router.message(Command("menu", "home"))
async def menu_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    text, markup = await _home_screen(db, message.from_user.id, language, settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    if callback.message is None:
        return
    await state.clear()
    language = await _language_for_callback(db, callback)
    text, markup = await _home_screen(db, callback.from_user.id, language, settings)
    await _safe_edit(callback.message, text, markup)
    await callback.answer()


@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    await state.clear()
    language = await _language_for_callback(db, callback)
    text, markup = await _home_screen(db, callback.from_user.id, language, settings)
    await _safe_edit(callback.message, text, markup)
    await callback.answer("Cancelled")


@router.message(Command("help"))
async def help_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    text = safe_t(language, "help_title", commands=command_help(language))
    if _is_admin(settings, message.from_user.id):
        text += "\n\n" + safe_t(language, "admin_help_title", commands=admin_help())
    await message.answer(text, reply_markup=_nav_keyboard())


@router.message(Command("adminhelp"))
async def admin_help_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        language = await _language_for_message(db, message)
        return await message.answer(safe_t(language, "admin_only"))
    language = await _language_for_message(db, message)
    await message.answer(safe_t(language, "admin_help_title", commands=admin_help()))


@router.message(Command("support", "contact"))
async def support_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    rows = []
    if settings.support_bot_link:
        rows.append([InlineKeyboardButton(text="📞 Contact Support", url=settings.support_bot_link)])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    await message.answer(
        safe_t(language, "support_intro"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(Command("updates", "channel"))
async def updates_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    channel_url = f"https://t.me/{settings.update_channel_username.lstrip('@')}"
    await message.answer(
        safe_t(language, "updates_intro"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Open Channel", url=channel_url)],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )


# ==========================================
# LANGUAGE
# ==========================================

@router.message(Command("language"))
async def language_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(safe_t(language, "choose_language"), reply_markup=language_keyboard())


@router.callback_query(F.data.startswith("language:"))
async def choose_language(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    chosen = callback.data.split(":", 1)[1]
    if chosen not in ("en", "hinglish"):
        return await callback.answer("Invalid option", show_alert=True)
    await db.set_language(callback.from_user.id, chosen)
    await callback.answer(safe_t(chosen, "language_saved"))
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, chosen):
        return
    text, markup = await _home_screen(db, callback.from_user.id, chosen, settings)
    await _safe_edit(callback.message, text, markup)


# ==========================================
# FAQ
# ==========================================

FAQ_PAGE_SIZE = 5


def _faq_keyboard(language: str, page: int, selected: int | None = None) -> InlineKeyboardMarkup:
    """The question list. Stays on screen after an answer is opened, so the
    user can read the next one without going back."""
    items = FAQS.get(language) or FAQS.get("en") or []
    pages = max(1, (len(items) + FAQ_PAGE_SIZE - 1) // FAQ_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * FAQ_PAGE_SIZE
    rows = []
    for offset, faq in enumerate(items[start:start + FAQ_PAGE_SIZE]):
        index = start + offset
        mark = "✅ " if index == selected else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{index + 1}. {faq.question[:48]}",
            callback_data=f"faq:item:{index}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"faq:page:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"faq:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _faq_text(language: str, page: int, index: int | None = None) -> str:
    items = FAQS.get(language) or FAQS.get("en") or []
    pages = max(1, (len(items) + FAQ_PAGE_SIZE - 1) // FAQ_PAGE_SIZE)
    header = safe_t(language, "faq_title", page=page + 1, pages=pages)

    if index is None or index < 0 or index >= len(items):
        return f"{header}\n\n{safe_t(language, 'faq_hint')}"

    faq = items[index]
    return (
        f"{header}\n\n"
        f"❓ <b>Q — {safe_html(faq.question)}</b>\n\n"
        f"💡 <b>Ans —</b> {safe_html(faq.answer)}\n\n"
        f"━━━━━━━━━━━━━━\n"
        f"{safe_t(language, 'faq_hint')}"
    )


@router.message(Command("faq"))
async def faq_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(_faq_text(language, 0), reply_markup=_faq_keyboard(language, 0))


@router.callback_query(F.data.startswith("faq:page:"))
async def faq_page(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    page = int(callback.data.rsplit(":", 1)[1])
    language = await _language_for_callback(db, callback)
    await _safe_edit(callback.message, _faq_text(language, page), _faq_keyboard(language, page))
    await callback.answer()


@router.callback_query(F.data.startswith("faq:item:"))
async def faq_item(callback: CallbackQuery, db: Database) -> None:
    """Edits the SAME message: answer on top, question list still below."""
    if callback.message is None:
        return
    index = int(callback.data.rsplit(":", 1)[1])
    language = await _language_for_callback(db, callback)
    items = FAQS.get(language) or FAQS.get("en") or []
    if index < 0 or index >= len(items):
        return await callback.answer("Not found", show_alert=True)
    page = index // FAQ_PAGE_SIZE
    await _safe_edit(
        callback.message,
        _faq_text(language, page, index),
        _faq_keyboard(language, page, selected=index),
    )
    await callback.answer()


# ==========================================
# CHANNEL GATE
# ==========================================

@router.callback_query(F.data == "gate:check")
async def gate_check(
    callback: CallbackQuery, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    member = await user_is_member(callback.bot, settings, callback.from_user.id)
    await db.set_membership(callback.from_user.id, member)
    if not member:
        return await callback.answer("⚠️ You have not joined yet.", show_alert=True)
    await db.resume_channel_gate_tasks(callback.from_user.id)
    with suppress(Exception):
        await forwarding.refresh_user(callback.from_user.id)
    text, markup = await _home_screen(db, callback.from_user.id, language, settings)
    await _safe_edit(callback.message, text, markup)
    await callback.answer("✅ Thank you for joining!")


# ==========================================
# ACCOUNT
# ==========================================

async def _account_text(db: Database, user_id: int, user, language: str) -> str:
    last_payment = await db.get_last_captured_payment(user_id)
    txn_id = last_payment["payment_id"] if last_payment and last_payment["payment_id"] else "—"
    connected = await db.has_active_session(user_id)
    expiry = (
        user["plan_expiry"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        if user and user["plan_expiry"] else "—"
    )
    started = user["created_at"].astimezone(IST).strftime("%d %b %Y") if user else "—"
    return safe_t(
        language, "account_details",
        name=_format_name(user),
        username=f"@{safe_html(user['username'])}" if user and user["username"] else "—",
        user_id=user_id,
        plan=str(user["plan"]).title() if user else "Free",
        plan_started=started,
        expiry=expiry,
        txn_id=safe_html(txn_id),
        payment="✅ Paid" if last_payment else "—",
        session="✅ Connected" if connected else "❌ Not connected",
        tasks=await db.count_tasks(user_id),
        forwarding=await db.daily_usage(user_id),
        membership="✅ Joined" if user and user["updates_channel_member"] else "❌ Not joined",
        user_language="English" if language == "en" else "Hinglish",
    )


def _account_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
        [InlineKeyboardButton(text="🔌 Disconnect", callback_data="auth:disconnect")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])


@router.message(Command("account", "myaccount"))
async def account_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    user = await db.get_user(message.from_user.id)
    await message.answer(
        await _account_text(db, message.from_user.id, user, language),
        reply_markup=_account_keyboard(),
    )


@router.callback_query(F.data == "menu:account")
async def menu_account(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    user = await db.get_user(callback.from_user.id)
    await _safe_edit(
        callback.message,
        await _account_text(db, callback.from_user.id, user, language),
        _account_keyboard(),
    )
    await callback.answer()


async def _refer_screen(bot: Bot, db: Database, user_id: int, language: str):
    """Referral screen.

    The link carries a short random CODE, not the Telegram user id — sharing
    ref_8844066493 publishes your account id to anyone who sees the link.
    The link is wrapped in <code> so a single tap copies it.
    """
    me = await bot.get_me()
    code = await db.ensure_referral_code(user_id)
    if not code:
        # Code generation failed (DB hiccup) — fall back so the screen still
        # works rather than showing a broken link.
        code = str(user_id)
    link = f"https://t.me/{me.username}?start=ref_{code}"
    summary = await db.referral_summary(user_id)

    text = (
        "🎁 <b>Refer &amp; Earn</b>\n\n"
        f"Earn <b>{int(REFERRAL_RATE * 100)}% commission</b> on every payment "
        "your referrals make — for life.\n\n"
        "🔗 <b>Your link</b> (tap to copy):\n"
        f"<code>{safe_html(link)}</code>\n\n"
        f"👥 Referrals: <b>{summary['joined']}</b>\n"
        f"💰 Unpaid earnings: <b>{format_paise(summary['unpaid_paise'])}</b>\n"
        f"🏆 Lifetime earned: <b>{format_paise(summary['unpaid_paise'] + summary['paid_paise'])}</b>\n\n"
        f"📊 Minimum payout: {format_paise(MIN_WITHDRAWAL_PAISE)}"
    )
    share = (
        f"https://t.me/share/url?url={link}"
        "&text=Auto-forward posts from any channel to yours. Free to start!"
    )
    rows = [
        [InlineKeyboardButton(text="💸 Withdraw", callback_data="wd:menu"),
         InlineKeyboardButton(text="🏦 Payment Method", callback_data="pm:menu")],
        [InlineKeyboardButton(text="📤 Share Link", url=share)],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("refer", "referral"))
async def refer_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    text, markup = await _refer_screen(message.bot, db, message.from_user.id, language)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "menu:refer")
async def menu_refer(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    text, markup = await _refer_screen(callback.bot, db, callback.from_user.id, language)
    await _safe_edit(callback.message, text, markup)
    await callback.answer()


# ==========================================
# LOGIN / CONNECT
# ==========================================

async def _track_login_msg(state: FSMContext, message_id: int) -> None:
    """Remembers a message that is part of the /connect flow so it can be wiped
    once login succeeds."""
    data = await state.get_data()
    ids: list[int] = list(data.get("login_msg_ids", []))
    ids.append(message_id)
    await state.update_data(login_msg_ids=ids)


async def _cleanup_login_msgs(message: Message, state: FSMContext) -> None:
    """Deletes every tracked message from the /connect flow — bot prompts and
    the user's own replies alike.

    Entirely best-effort: Telegram refuses deletes older than 48 hours and
    silently ignores already-deleted ids, neither of which should surface to
    the user right after a successful login.
    """
    data = await state.get_data()
    for msg_id in list(data.get("login_msg_ids", [])):
        with suppress(Exception):
            await message.bot.delete_message(message.chat.id, msg_id)
    with suppress(Exception):
        await message.delete()


async def _finish_login_success(
    message: Message, state: FSMContext, db: Database, forwarding: ForwardingEngine,
    settings: Settings, account_info: dict, language: str,
) -> None:
    """Shared tail-end for a successful login, with or without 2FA: wipe the
    whole flow and leave exactly one congratulations message."""
    await _cleanup_login_msgs(message, state)
    await state.clear()
    with suppress(Exception):
        await forwarding.refresh_user(message.from_user.id)
    await _notify_admins(
        message.bot, settings,
        f"🔌 <b>Account connected</b>\nID: <code>{message.from_user.id}</code>",
    )

    user = await db.get_user(message.from_user.id)
    phone = str(account_info.get("phone") or "").strip()
    if phone and not phone.startswith("+"):
        phone = f"+{phone}"
    tg_username = account_info.get("username")

    await message.answer(
        safe_t(
            language, "login_congrats",
            name=_format_name(user),
            phone=safe_html(phone or "—"),
            username=f"@{safe_html(tg_username)}" if tg_username else "—",
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Create Task", callback_data="task:create")],
            [InlineKeyboardButton(text="💎 View Plans", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )


@router.message(Command("connect", "login"))
async def connect_command(
    message: Message, state: FSMContext, db: Database, settings: Settings, telethon: TelethonService,
) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    if await db.has_active_session(message.from_user.id):
        await message.answer(
            safe_t(language, "already_connected"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=safe_t(language, "reconnect_anyway"), callback_data="connect:force")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
        )
        return
    await telethon.cancel_login(message.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await state.update_data(login_msg_ids=[message.message_id])
    prompt = await message.answer(
        safe_t(language, "login_phone"), reply_markup=_nav_keyboard(include_cancel=True),
    )
    await _track_login_msg(state, prompt.message_id)


@router.callback_query(F.data == "menu:connect")
async def menu_connect(
    callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext,
    telethon: TelethonService,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        return
    if await db.has_active_session(callback.from_user.id):
        await _safe_edit(
            callback.message,
            safe_t(language, "already_connected"),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=safe_t(language, "reconnect_anyway"), callback_data="connect:force")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
        )
        return await callback.answer()
    await telethon.cancel_login(callback.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await state.update_data(login_msg_ids=[callback.message.message_id])
    await _safe_edit(
        callback.message, safe_t(language, "login_phone"), _nav_keyboard(include_cancel=True),
    )
    await callback.answer()


@router.callback_query(F.data == "connect:force")
async def connect_force(
    callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    await telethon.cancel_login(callback.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await state.update_data(login_msg_ids=[callback.message.message_id])
    await _safe_edit(
        callback.message, safe_t(language, "login_phone"), _nav_keyboard(include_cancel=True),
    )
    await callback.answer()


@router.message(LoginStates.waiting_phone)
async def login_phone(
    message: Message, state: FSMContext, telethon: TelethonService, db: Database,
) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    await _track_login_msg(state, message.message_id)
    phone = message.text.strip().replace(" ", "").replace("-", "")
    if phone == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        return await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())
    if not re.fullmatch(r"\+\d{8,15}", phone):
        warn = await message.answer(
            safe_t(language, "invalid_phone"), reply_markup=_nav_keyboard(include_cancel=True),
        )
        await _track_login_msg(state, warn.message_id)
        return
    try:
        await telethon.start_phone_login(message.from_user.id, phone)
    except ValueError as exc:
        warn = await message.answer(f"⚠️ {safe_html(exc)}")
        await _track_login_msg(state, warn.message_id)
        return
    except Exception:
        logger.exception("Phone login failed for %s", message.from_user.id)
        await state.clear()
        return await message.answer(safe_t(language, "login_failed"), reply_markup=_nav_keyboard())
    await state.set_state(LoginStates.waiting_pin)
    prompt = await message.answer(
        safe_t(language, "login_pin"), reply_markup=_nav_keyboard(include_cancel=True),
    )
    await _track_login_msg(state, prompt.message_id)


@router.message(LoginStates.waiting_pin)
async def login_pin(
    message: Message, state: FSMContext, telethon: TelethonService, db: Database,
    forwarding: ForwardingEngine, settings: Settings,
) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    await _track_login_msg(state, message.message_id)
    raw_pin = message.text.strip()
    if raw_pin == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        return await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())

    # The user is asked to type the OTP as "PIN12345". Telegram's own login
    # notification warns against sharing a bare code, so the prefix makes it
    # obvious this is a deliberate action rather than a phishing attempt.
    pin = raw_pin[3:].strip() if raw_pin.upper().startswith("PIN") else raw_pin
    try:
        result = await telethon.submit_pin(message.from_user.id, pin)
    except ValueError as exc:
        warn = await message.answer(f"⚠️ {safe_html(exc)}")
        await _track_login_msg(state, warn.message_id)
        return

    if result == "2fa_required":
        await state.set_state(LoginStates.waiting_2fa)
        prompt = await message.answer(
            safe_t(language, "login_2fa"), reply_markup=_nav_keyboard(include_cancel=True),
        )
        await _track_login_msg(state, prompt.message_id)
        return

    await _finish_login_success(message, state, db, forwarding, settings, result, language)


@router.message(LoginStates.waiting_2fa)
async def login_2fa(
    message: Message, state: FSMContext, telethon: TelethonService, db: Database,
    forwarding: ForwardingEngine, settings: Settings,
) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)

    # SECURITY: the cloud password is deleted the instant it arrives — before it
    # is even validated — so it never sits in the chat history, not even briefly
    # while we wait on Telegram.
    password = message.text
    with suppress(Exception):
        await message.delete()

    if password.strip() == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        return await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())

    try:
        result = await telethon.submit_2fa(message.from_user.id, password)
    except ValueError as exc:
        warn = await message.answer(f"⚠️ {safe_html(exc)}")
        await _track_login_msg(state, warn.message_id)
        return

    await _finish_login_success(message, state, db, forwarding, settings, result, language)


@router.message(Command("disconnect"))
async def disconnect_command(
    message: Message, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine, settings: Settings,
) -> None:
    language = await _language_for_message(db, message)
    await message.answer(
        safe_t(language, "disconnect_confirm"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Disconnect", callback_data="auth:disconnect")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )


@router.callback_query(F.data == "auth:disconnect")
async def auth_disconnect_callback(
    callback: CallbackQuery, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine, settings: Settings,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    await forwarding.remove_user(callback.from_user.id)
    await telethon.disconnect(callback.from_user.id)
    await _safe_edit(callback.message, safe_t(language, "disconnect_done"), _nav_keyboard())
    await callback.answer()


# ==========================================
# FILE UPLOAD (ATTACH CUSTOM FILE)
# ==========================================

def _can_upload_file(plan_name: str) -> bool:
    from .plans import F_ATTACH_FILE, plan_has
    return plan_has(plan_name, F_ATTACH_FILE)


async def _start_upload(message_obj, db: Database, settings: Settings, user_id: int, language: str) -> None:
    user = await db.get_user(user_id)
    plan_name = str(user["plan"]) if user else "free"
    if not _can_upload_file(plan_name):
        text = safe_t(language, "upload_not_platinum")
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ])
    elif not settings.file_storage_channel_id:
        text = safe_t(language, "upload_no_channel")
        markup = _nav_keyboard()
    else:
        text = safe_t(language, "upload_prompt", max=settings.max_file_size_mb)
        markup = _nav_keyboard(include_cancel=True)

    if hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        with suppress(TelegramBadRequest):
            await message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
    await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("upload_file"))
async def upload_file_cmd(
    message: Message, state: FSMContext, db: Database, settings: Settings,
) -> None:
    language = await _language_for_message(db, message)
    user = await db.get_user(message.from_user.id)
    # Check the plan BEFORE requiring a connected account, otherwise lower-tier
    # users get the wrong "connect your account" flow instead of the upgrade note.
    if not _can_upload_file(str(user["plan"]) if user else "free"):
        return await _start_upload(message, db, settings, message.from_user.id, language)
    connect_msg = await _require_connected(db, message.from_user.id, language)
    if connect_msg:
        return await message.answer(connect_msg, reply_markup=_connect_required_keyboard())
    if settings.file_storage_channel_id:
        await state.set_state(UploadStates.waiting_file)
    await _start_upload(message, db, settings, message.from_user.id, language)


@router.callback_query(F.data == "menu:upload")
async def menu_upload(
    callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    user = await db.get_user(callback.from_user.id)
    if not _can_upload_file(str(user["plan"]) if user else "free"):
        await _start_upload(callback.message, db, settings, callback.from_user.id, language)
        return await callback.answer()
    connect_msg = await _require_connected(db, callback.from_user.id, language)
    if connect_msg:
        await _safe_edit(callback.message, connect_msg, _connect_required_keyboard())
        return await callback.answer()
    if settings.file_storage_channel_id:
        await state.set_state(UploadStates.waiting_file)
    await _start_upload(callback.message, db, settings, callback.from_user.id, language)
    await callback.answer()


async def _do_store_upload(
    bot: Bot, db: Database, telethon: TelethonService, settings: Settings,
    user_id: int, file_name: str, ext: str, size: int, file_id: str | None,
    src_chat_id: int, src_message_id: int, language: str, reply_chat_id: int,
) -> None:
    """Downloads the file, copies it to the storage channel, then records it.

    The storage channel is the source of truth — only its message id is kept in
    the database. Railway's local disk is ephemeral, so forwarding.py restores
    the file from that channel whenever the local cache is missing.
    """
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)
    safe_file_name = os.path.basename(file_name).replace("\x00", "_")
    local_path = uploads_dir / f"{user_id}_{uuid4().hex[:8]}_{safe_file_name}"

    BOT_API_LIMIT = 20 * 1024 * 1024
    try:
        if size <= BOT_API_LIMIT and file_id:
            tg_file = await bot.get_file(file_id)
            await bot.download_file(tg_file.file_path, destination=str(local_path))
        else:
            ok = await telethon.download_media_big(
                settings.telegram_bot_token, src_chat_id, src_message_id, str(local_path),
            )
            if not ok:
                raise RuntimeError("MTProto download returned no media")
    except Exception as exc:
        logger.error(f"upload download failed: {exc}")
        await _notify_admins(bot, settings, f"⚠️ Upload download failed for {user_id}: {exc}")
        await bot.send_message(
            reply_chat_id,
            "⚠️ Download failed. Please try again.\n"
            "If it keeps failing, send the file as a Document (File), not compressed.",
        )
        return

    try:
        sent = await bot.send_document(
            settings.file_storage_channel_id,
            document=FSInputFile(str(local_path)),
            caption=f"storage:{user_id}",
        )
        channel_msg_id = sent.message_id
    except Exception as exc:
        logger.warning(f"storage-channel copy failed: {exc}")
        await _notify_admins(
            bot, settings,
            f"⚠️ Storage channel copy failed for user {user_id}: {exc}\n"
            f"Check the bot is an ADMIN in the storage channel ({settings.file_storage_channel_id}).",
        )
        with suppress(Exception):
            local_path.unlink(missing_ok=True)
        await bot.send_message(
            reply_chat_id,
            "⚠️ The file could not be saved to the storage channel, so it was not activated. "
            "Please try again.",
        )
        return

    old_stored = await db.get_stored_file(user_id)
    if old_stored and old_stored["channel_message_id"]:
        with suppress(Exception):
            await bot.delete_message(
                settings.file_storage_channel_id, int(old_stored["channel_message_id"]),
            )
    with suppress(Exception):
        local_path.unlink(missing_ok=True)

    await db.save_stored_file(user_id, file_name, ext, size, None, channel_msg_id, file_id)
    size_str = f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
    await bot.send_message(
        reply_chat_id,
        safe_t(language, "upload_success", name=safe_html(file_name), size=size_str),
        reply_markup=_nav_keyboard(), parse_mode="HTML",
    )


@router.message(UploadStates.waiting_file)
async def upload_receive(
    message: Message, state: FSMContext, db: Database,
    telethon: TelethonService, settings: Settings,
) -> None:
    language = await _language_for_message(db, message)
    user = await db.get_user(message.from_user.id)
    # Re-check at submission time: the plan may have expired while the upload
    # state was still active.
    if not _can_upload_file(str(user["plan"]) if user else "free"):
        await state.clear()
        return await message.answer(safe_t(language, "upload_not_platinum"), reply_markup=_nav_keyboard())
    if message.text and message.text.strip() == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())

    doc = message.document or (message.photo[-1] if message.photo else None) or message.video or message.audio
    if doc is None:
        return await message.answer(
            safe_t(language, "upload_prompt", max=settings.max_file_size_mb), parse_mode="HTML",
        )

    file_name = getattr(doc, "file_name", None) or f"file_{uuid4().hex[:8]}.jpg"
    ext = (os.path.splitext(file_name)[1].lower().lstrip(".") or "bin")[:32]
    size = int(getattr(doc, "file_size", 0) or 0)
    if size > settings.max_file_size_mb * 1024 * 1024:
        return await message.answer(
            safe_t(language, "upload_too_big", max=settings.max_file_size_mb), parse_mode="HTML",
        )

    # Only one stored file per user — confirm before replacing rather than
    # silently destroying whatever they had.
    existing = await db.get_stored_file(message.from_user.id)
    if existing is not None:
        await state.update_data(pending_upload={
            "file_name": file_name, "ext": ext, "size": size,
            "file_id": getattr(doc, "file_id", None),
            "chat_id": message.chat.id, "message_id": message.message_id,
        })
        return await message.answer(
            safe_t(language, "upload_replace_confirm",
                   old_name=safe_html(existing["file_name"]), new_name=safe_html(file_name)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Yes, Replace", callback_data="upload:replace:yes")],
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="upload:replace:no")],
            ]),
            parse_mode="HTML",
        )

    await state.clear()
    progress = await message.answer("⏳ <b>Uploading your file…</b>", parse_mode="HTML")
    async with _busy(message.bot, message.chat.id):
        await _do_store_upload(
            message.bot, db, telethon, settings, message.from_user.id,
            file_name, ext, size, getattr(doc, "file_id", None),
            message.chat.id, message.message_id, language, message.chat.id,
        )
    with suppress(Exception):
        await progress.delete()


@router.callback_query(F.data.startswith("upload:replace:"))
async def upload_replace_cb(
    callback: CallbackQuery, state: FSMContext, db: Database,
    telethon: TelethonService, settings: Settings,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    user = await db.get_user(callback.from_user.id)
    if not _can_upload_file(str(user["plan"]) if user else "free"):
        await state.clear()
        await _safe_edit(callback.message, safe_t(language, "upload_not_platinum"), _nav_keyboard())
        return await callback.answer()

    decision = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    pending = data.get("pending_upload")
    await state.clear()
    if decision != "yes" or not pending:
        await _safe_edit(callback.message, "↩️ Cancelled.", _nav_keyboard())
        return await callback.answer()

    with suppress(TelegramBadRequest):
        await callback.message.edit_text("⏳ <b>Uploading your file…</b>", reply_markup=None)
    async with _busy(callback.bot, callback.message.chat.id):
        await _do_store_upload(
            callback.bot, db, telethon, settings, callback.from_user.id,
            pending["file_name"], pending["ext"], pending["size"], pending.get("file_id"),
            pending["chat_id"], pending["message_id"], language, callback.message.chat.id,
        )
    await callback.answer()


# ==========================================
# TASKS
# ==========================================

async def _render_tasks(message_obj, db: Database, user_id: int) -> None:
    user = await db.get_user(user_id)
    language = language_for(user["preferred_language"]) if user else "en"
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    tasks = await db.list_tasks(user_id)

    if not tasks:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ])
        text = safe_t(language, "no_tasks_short")
    else:
        lines = [safe_t(language, "tasks_title"), ""]
        rows = []
        for task in tasks:
            sources = _json_field(task["sources"], [])
            destinations = _json_field(task["destinations"], [])
            status = "⏸️" if task["is_paused"] else "▶️"
            lines.append(
                f"{status} <b>{safe_html(task['task_name'])}</b> — "
                f"{len(sources)} src → {len(destinations)} dst"
            )
            rows.append([InlineKeyboardButton(
                text=f"{status} {task['task_name'][:24]}", callback_data=f"st:task:{task['id']}",
            )])
        lines.append("")
        lines.append(
            f"📊 {len(tasks)}/{plan.tasks} tasks used on the <b>{plan.name}</b> plan"
        )
        if len(tasks) < plan.tasks:
            rows.append([InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")])
        rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
        text = "\n".join(lines)
        markup = InlineKeyboardMarkup(inline_keyboard=rows)

    if hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        with suppress(TelegramBadRequest):
            await message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
    await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")


@router.message(Command("tasks", "viewtasks"))
async def view_tasks(message: Message, db: Database) -> None:
    await _render_tasks(message, db, message.from_user.id)


@router.callback_query(F.data == "menu:tasks")
async def menu_tasks(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        return
    await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer()


async def _can_create_task(db: Database, user_id: int) -> tuple[bool, str]:
    user = await db.get_user(user_id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    used = await db.count_tasks(user_id)
    if used >= plan.tasks:
        return False, (
            f"⚠️ You've used all {plan.tasks} tasks on the <b>{plan.name}</b> plan.\n\n"
            f"Upgrade for more tasks."
        )
    return True, ""


@router.message(Command("newtask", "createtask"))
async def new_task_cmd(
    message: Message, state: FSMContext, db: Database, settings: Settings,
) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    connect_msg = await _require_connected(db, message.from_user.id, language)
    if connect_msg:
        return await message.answer(connect_msg, reply_markup=_connect_required_keyboard())
    allowed, warning = await _can_create_task(db, message.from_user.id)
    if not allowed:
        return await message.answer(warning, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Upgrade", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]), parse_mode="HTML")
    await state.set_state(TaskStates.waiting_name)
    await message.answer(safe_t(language, "task_name"), reply_markup=_nav_keyboard(include_cancel=True))


@router.callback_query(F.data == "task:create")
async def task_create_cb(
    callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        return
    connect_msg = await _require_connected(db, callback.from_user.id, language)
    if connect_msg:
        await _safe_edit(callback.message, connect_msg, _connect_required_keyboard())
        return await callback.answer()
    allowed, warning = await _can_create_task(db, callback.from_user.id)
    if not allowed:
        await _safe_edit(callback.message, warning, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Upgrade", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]))
        return await callback.answer()
    await state.set_state(TaskStates.waiting_name)
    await _safe_edit(callback.message, safe_t(language, "task_name"), _nav_keyboard(include_cancel=True))
    await callback.answer()


@router.message(TaskStates.waiting_name)
async def task_name(
    message: Message, state: FSMContext, db: Database, telethon: TelethonService,
) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    await state.update_data(task_name=message.text.strip()[:120])
    await state.set_state(TaskStates.waiting_source)
    await _render_chat_picker(message, db, telethon, state, message.from_user.id, "src", language)


def _text_or_forwarded_chat_id(message: Message) -> str | None:
    if message.text:
        return message.text.strip()
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin is not None else None
    if chat is not None and getattr(chat, "id", None) is not None:
        return str(chat.id)
    legacy_chat = getattr(message, "forward_from_chat", None)
    if legacy_chat is not None and getattr(legacy_chat, "id", None) is not None:
        return str(legacy_chat.id)
    return None


# ==========================================
# CHAT PICKER
# ==========================================

def _protected_block_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Back", callback_data="menu:tasks")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])


def _is_protected(entity: dict) -> bool:
    """True when the chat has "Restrict saving content" enabled.

    Only SOURCES are refused. A protected destination is harmless — the
    restriction is about copying content OUT of a chat, not into one.
    """
    return bool(entity.get("protected"))


CHANNEL_INPUT_RE = re.compile(
    r"^(?:@[\w]{2,64}|https?://t\.me/\S+|t\.me/\S+|-?\d{5,})$", re.IGNORECASE,
)

PICKER_DIALOG_LIMIT = 15
PICKER_BUTTONS_PER_ROW = 5


def _picker_field_state(data: dict, field: str) -> tuple[list, list]:
    selected = list(data.get("sources" if field == "src" else "destinations") or [])
    dialogs = list(data.get("picker_dialogs") or [])
    return selected, dialogs


def _picker_keyboard(dialogs: list, selected_ids: set[int], field: str, language: str) -> InlineKeyboardMarkup:
    """A numeric keypad — one button per listed chat — so nothing has to be
    typed. Selected entries show a tick; tapping again deselects."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for idx, d in enumerate(dialogs):
        try:
            did = int(d.get("id", 0))
        except (TypeError, ValueError):
            continue
        number = idx + 1
        label = f"✅ {number}" if did in selected_ids else str(number)
        row.append(InlineKeyboardButton(text=label, callback_data=f"pick:{field}:{number}"))
        if len(row) == PICKER_BUTTONS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text=safe_t(language, "picker_done"), callback_data=f"pick:done:{field}"),
        InlineKeyboardButton(text=safe_t(language, "picker_refresh"), callback_data=f"pick:refresh:{field}"),
    ])
    rows.append([InlineKeyboardButton(text=safe_t(language, "picker_cancel"), callback_data="flow:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_chat_picker(
    message_obj, db: Database, telethon: TelethonService, state: FSMContext,
    user_id: int, field: str, language: str, *, refresh: bool = False, edit: bool = False,
) -> None:
    data = await state.get_data()
    selected, dialogs = _picker_field_state(data, field)
    if refresh or not dialogs:
        # Reading the dialog list is a Telegram round-trip and can take a few
        # seconds; show a spinner so the screen never looks frozen.
        if edit and hasattr(message_obj, "edit_text"):
            with suppress(Exception):
                await _safe_edit(message_obj, "⏳ <b>Loading your chats…</b>", None)
        async with _busy(message_obj.bot, message_obj.chat.id):
            dialogs = await telethon.get_top_dialogs(user_id, limit=PICKER_DIALOG_LIMIT)
        await state.update_data(picker_dialogs=dialogs)

    user = await db.get_user(user_id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    limit = plan.sources_per_task if field == "src" else plan.destinations_per_task
    title_key = "picker_title_src" if field == "src" else "picker_title_dst"

    selected_ids = {int(e.get("id", 0)) for e in selected}
    lines: list[str] = [safe_t(language, title_key, limit=limit), ""]
    for idx, d in enumerate(dialogs):
        try:
            did = int(d.get("id", 0))
        except (TypeError, ValueError):
            continue
        label = safe_html(str(d.get("title") or d.get("username") or did))[:40]
        forum = " 🧵" if d.get("is_forum") else ""
        mark = " ✅" if did in selected_ids else ""
        lines.append(f"{idx + 1}. {label}{forum}{mark}")

    sel_titles = [safe_html(str(e.get("title") or e.get("username") or e.get("id")))[:30] for e in selected]
    lines.append("")
    lines.append(f"✅ <b>Selected ({len(selected)}/{limit}):</b> {', '.join(sel_titles) if sel_titles else '—'}")
    lines.append("")
    lines.append(safe_t(language, "picker_instructions"))

    text = "\n".join(lines)
    keyboard = _picker_keyboard(dialogs, selected_ids, field, language)
    if not dialogs:
        text = safe_t(language, "picker_empty")
        keyboard = _nav_keyboard(include_cancel=True)

    if edit and hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        try:
            await _safe_edit(message_obj, text, keyboard)
            return
        except TelegramBadRequest:
            pass
    await message_obj.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _picker_toggle_number(
    message_obj, state: FSMContext, db: Database, telethon: TelethonService,
    user_id: int, field: str, language: str, number: int, *, edit: bool = False,
) -> bool:
    data = await state.get_data()
    key = "sources" if field == "src" else "destinations"
    selected, dialogs = _picker_field_state(data, field)
    idx = number - 1
    if idx < 0 or idx >= len(dialogs):
        return False

    entity = dialogs[idx]
    eid = int(entity.get("id", 0))

    # Refuse protected chats as SOURCES, before they can be selected.
    if field == "src" and _is_protected(entity):
        await message_obj.answer(
            safe_t(language, "protected_source_blocked",
                   name=safe_html(entity.get("title") or eid)),
            parse_mode="HTML",
        )
        return True

    sel_ids = {int(e.get("id", 0)) for e in selected}
    if eid in sel_ids:
        selected = [e for e in selected if int(e.get("id", 0)) != eid]
    else:
        user = await db.get_user(user_id)
        plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
        limit = plan.sources_per_task if field == "src" else plan.destinations_per_task
        if len(selected) >= limit:
            await message_obj.answer(
                safe_t(language, "picker_limit_reached", limit=limit), parse_mode="HTML",
            )
            return True
        selected.append(entity)

    await state.update_data({key: selected})
    await _render_chat_picker(message_obj, db, telethon, state, user_id, field, language, edit=edit)
    return True


async def _reply_or_edit(message_obj, text: str, keyboard, edit: bool) -> None:
    if edit and hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        try:
            await _safe_edit(message_obj, text, keyboard)
            return
        except TelegramBadRequest:
            pass
    await message_obj.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def _finish_sources(
    message_obj, state: FSMContext, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine, user_id: int, language: str, *, edit: bool = False,
) -> str | None:
    data = await state.get_data()
    sources = list(data.get("sources") or [])
    edit_task_id = data.get("edit_task_id")
    is_editing = edit_task_id is not None and data.get("edit_field") == "sources"

    if not sources:
        if not edit:
            await message_obj.answer(safe_t(language, "picker_need_source"), parse_mode="HTML")
        return safe_t(language, "picker_need_source_toast")

    if is_editing:
        changed = await db.update_task_sources(user_id, int(edit_task_id), sources)
        await state.clear()
        if changed:
            await forwarding.refresh_task(int(edit_task_id))
        await _reply_or_edit(
            message_obj,
            safe_t(language, "sources_updated") if changed else safe_t(language, "generic_error"),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📤 Edit Destinations", callback_data=f"task:edit-dest:{edit_task_id}")],
                [InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{edit_task_id}")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            edit,
        )
        return None

    await state.update_data(sources=sources, destinations=[], picker_dialogs=None)
    await state.set_state(TaskStates.waiting_destination)
    await _render_chat_picker(message_obj, db, telethon, state, user_id, "dst", language, edit=edit)
    return None


async def _finish_destinations(
    message_obj, state: FSMContext, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine, settings: Settings, user_id: int, language: str,
    *, edit: bool = False,
) -> str | None:
    data = await state.get_data()
    destinations = list(data.get("destinations") or [])
    edit_task_id = data.get("edit_task_id")
    is_editing = edit_task_id is not None and data.get("edit_field") == "destinations"

    if not destinations:
        if not edit:
            await message_obj.answer(safe_t(language, "picker_need_destination"), parse_mode="HTML")
        return safe_t(language, "picker_need_destination_toast")

    if is_editing:
        changed = await db.update_task_destinations(user_id, int(edit_task_id), destinations)
        await state.clear()
        if changed:
            await forwarding.refresh_task(int(edit_task_id))
        await _reply_or_edit(
            message_obj,
            safe_t(language, "destinations_updated") if changed else safe_t(language, "generic_error"),
            _nav_keyboard(back=f"st:task:{edit_task_id}"), edit,
        )
        return None

    task_name_value = str(data.get("task_name") or "Task")
    task_id = await db.create_task_multi(
        user_id, task_name_value, list(data.get("sources") or []), destinations,
    )
    await state.clear()
    await forwarding.refresh_task(task_id)

    user = await db.get_user(user_id)
    source_text = ", ".join(
        safe_html(str(item.get("title") or item.get("username") or item.get("id")))
        for item in data.get("sources", []) if isinstance(item, dict)
    ) or "—"
    destination_text = ", ".join(
        safe_html(str(item.get("title") or item.get("username") or item.get("id")))
        for item in destinations if isinstance(item, dict)
    ) or "—"
    await _notify_admins(
        message_obj.bot, settings,
        f"➕ <b>New forwarding task created</b>\n"
        f"Task ID: <code>{task_id}</code>\n"
        f"Task name: {safe_html(task_name_value)}\n"
        f"User: {_format_name(user)}\n"
        f"Username: @{safe_html((user['username'] if user else None) or '—')}\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Plan: {safe_html(str(user['plan']).title() if user else 'Free')}\n"
        f"Sources ({len(data.get('sources', []))}): {source_text}\n"
        f"Destinations ({len(destinations)}): {destination_text}\n"
        f"Status: ▶️ Active",
    )

    # The user never sees the raw internal task id — just their own task name.
    await _reply_or_edit(
        message_obj,
        safe_t(language, "task_created", task_name=safe_html(task_name_value)),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Configure Settings", callback_data=f"st:task:{task_id}")],
            [InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
        edit,
    )
    return None


@router.callback_query(F.data.startswith("pick:"))
async def picker_callback(
    callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine, settings: Settings,
) -> None:
    if callback.message is None:
        return await callback.answer()
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return await callback.answer()
    action, value = parts[1], parts[2]
    language = await _language_for_callback(db, callback)

    current = await state.get_state()
    if current not in {TaskStates.waiting_source.state, TaskStates.waiting_destination.state}:
        # Stale keyboard from an earlier, already-finished flow.
        return await callback.answer(safe_t(language, "picker_expired"), show_alert=True)

    if action == "refresh":
        field = value if value in {"src", "dst"} else "src"
        await _render_chat_picker(
            callback.message, db, telethon, state, callback.from_user.id,
            field, language, refresh=True, edit=True,
        )
        return await callback.answer(safe_t(language, "picker_refreshed"))

    if action == "done":
        field = value if value in {"src", "dst"} else "src"
        if field == "src":
            toast = await _finish_sources(
                callback.message, state, db, telethon, forwarding, callback.from_user.id,
                language, edit=True,
            )
        else:
            toast = await _finish_destinations(
                callback.message, state, db, telethon, forwarding, settings,
                callback.from_user.id, language, edit=True,
            )
        return await callback.answer(toast or "")

    if action in {"src", "dst"}:
        if not value.isdigit():
            return await callback.answer()
        handled = await _picker_toggle_number(
            callback.message, state, db, telethon, callback.from_user.id,
            action, language, int(value), edit=True,
        )
        if not handled:
            return await callback.answer(safe_t(language, "picker_expired"), show_alert=True)
        return await callback.answer()

    await callback.answer()


@router.message(TaskStates.waiting_source)
async def task_source(
    message: Message, state: FSMContext, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine,
) -> None:
    text = _text_or_forwarded_chat_id(message)
    if not text:
        return
    language = await _language_for_message(db, message)
    if text == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())

    data = await state.get_data()
    sources = list(data.get("sources", []))
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        return await _finish_sources(
            message, state, db, telethon, forwarding, message.from_user.id, language,
        )

    if text.isdigit():
        handled = await _picker_toggle_number(
            message, state, db, telethon, message.from_user.id, "src", language, int(text),
        )
        if handled:
            return
        return await message.answer(safe_t(language, "picker_bad_number"), parse_mode="HTML")

    if len(sources) >= plan.sources_per_task:
        return await message.answer(
            safe_t(language, "picker_limit_reached", limit=plan.sources_per_task), parse_mode="HTML",
        )
    if not CHANNEL_INPUT_RE.match(text):
        return await message.answer(safe_t(language, "invalid_channel_format"), parse_mode="HTML")
    try:
        async with _busy(message.bot, message.chat.id):
            entity = await telethon.validate_for_user(message.from_user.id, text)
    except ValueError as exc:
        return await message.answer(f"⚠️ {safe_html(exc)}")
    if _is_protected(entity):
        return await message.answer(
            safe_t(language, "protected_source_blocked",
                   name=safe_html(entity.get("title") or text)),
            reply_markup=_protected_block_markup(),
        )
    if any(int(e.get("id", 0)) == int(entity.get("id", 0)) for e in sources):
        return await message.answer(safe_t(language, "picker_already_added"), parse_mode="HTML")

    sources.append(entity)
    await state.update_data(sources=sources)
    if len(sources) < plan.sources_per_task:
        await _render_chat_picker(message, db, telethon, state, message.from_user.id, "src", language)
    else:
        await _finish_sources(message, state, db, telethon, forwarding, message.from_user.id, language)


@router.message(TaskStates.waiting_destination)
async def task_destination(
    message: Message, state: FSMContext, db: Database, telethon: TelethonService,
    forwarding: ForwardingEngine, settings: Settings,
) -> None:
    text = _text_or_forwarded_chat_id(message)
    if not text:
        return
    language = await _language_for_message(db, message)
    if text == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())

    data = await state.get_data()
    destinations = list(data.get("destinations", []))
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        return await _finish_destinations(
            message, state, db, telethon, forwarding, settings, message.from_user.id, language,
        )

    if text.isdigit():
        handled = await _picker_toggle_number(
            message, state, db, telethon, message.from_user.id, "dst", language, int(text),
        )
        if handled:
            return
        return await message.answer(safe_t(language, "picker_bad_number"), parse_mode="HTML")

    if len(destinations) >= plan.destinations_per_task:
        return await message.answer(
            safe_t(language, "picker_limit_reached", limit=plan.destinations_per_task), parse_mode="HTML",
        )
    if not CHANNEL_INPUT_RE.match(text):
        return await message.answer(safe_t(language, "invalid_channel_format"), parse_mode="HTML")
    try:
        async with _busy(message.bot, message.chat.id):
            destination = await telethon.validate_for_user(message.from_user.id, text)
    except ValueError as exc:
        return await message.answer(f"⚠️ {safe_html(exc)}")
    if any(int(e.get("id", 0)) == int(destination.get("id", 0)) for e in destinations):
        return await message.answer(safe_t(language, "picker_already_added"), parse_mode="HTML")

    destinations.append(destination)
    await state.update_data(destinations=destinations)
    if len(destinations) < plan.destinations_per_task:
        return await _render_chat_picker(
            message, db, telethon, state, message.from_user.id, "dst", language,
        )
    await _finish_destinations(
        message, state, db, telethon, forwarding, settings, message.from_user.id, language,
    )


# ==========================================
# TASK ACTIONS
# ==========================================

@router.message(Command("pause", "resume", "deletetask"))
async def task_action(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split()
    cmd = parts[0].lstrip("/").lower() if parts else ""
    language = await _language_for_message(db, message)

    if len(parts) == 1:
        tasks = await db.list_tasks(message.from_user.id)
        if cmd == "pause":
            targets = [t_ for t_ in tasks if not t_["is_paused"]]
            title = "⏸️ <b>Pause a Task</b>\n\nSelect a task to pause:"
        elif cmd == "resume":
            targets = [t_ for t_ in tasks if t_["is_paused"]]
            title = "▶️ <b>Resume a Task</b>\n\nSelect a task to resume:"
        else:
            targets = tasks
            title = "🗑️ <b>Delete a Task</b>\n\nSelect a task to delete:"

        if not targets:
            return await message.answer(safe_t(language, "no_tasks_short"), reply_markup=_nav_keyboard())

        rows = []
        prefix = {"pause": "task:pause:", "resume": "task:resume:"}.get(cmd, "task:delete:")
        for t_ in targets[:20]:
            label = f"{'⏸️' if t_['is_paused'] else '▶️'} {t_['task_name'][:28]}"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}{t_['id']}")])
        rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
        return await message.answer(
            title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
        )

    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer(
            f"Usage: <code>/{cmd} &lt;task_id&gt;</code>\n\n"
            f"Or send <code>/{cmd}</code> without an ID to pick from buttons.",
            parse_mode="HTML",
        )

    task_id = int(parts[1])
    if cmd == "deletetask":
        changed = await db.delete_task(message.from_user.id, task_id)
        if changed:
            await forwarding.remove_task(task_id)
        await message.answer("🗑️ Deleted." if changed else "⚠️ Not found.")
    else:
        paused = cmd == "pause"
        changed = await db.set_task_paused(
            message.from_user.id, task_id, paused, "user" if paused else None,
        )
        if changed:
            await forwarding.refresh_task(task_id)
        await message.answer(
            "⏸️ Paused." if changed and paused else "▶️ Resumed." if changed else "⚠️ Not found."
        )


@router.callback_query(F.data.startswith("task:pause:") | F.data.startswith("task:resume:"))
async def task_pause_resume_cb(
    callback: CallbackQuery, db: Database, forwarding: ForwardingEngine,
) -> None:
    _, action, task_id_str = callback.data.split(":")
    task_id = int(task_id_str)
    paused = action == "pause"
    changed = await db.set_task_paused(
        callback.from_user.id, task_id, paused, "user" if paused else None,
    )
    if changed:
        await forwarding.refresh_task(task_id)
    if callback.message:
        await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer(f"Task {action}d" if changed else "Not found")


@router.callback_query(F.data.startswith("task:delete:"))
async def task_delete_prompt(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    language = await _language_for_callback(db, callback)
    await _safe_edit(
        callback.message,
        safe_t(language, "delete_confirm", name=safe_html(task["task_name"])),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Delete", callback_data=f"task:delete-confirm:{task_id}")],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="menu:tasks")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:delete-confirm:"))
async def task_delete_confirm_cb(
    callback: CallbackQuery, db: Database, forwarding: ForwardingEngine,
) -> None:
    task_id = int(callback.data.split(":")[2])
    # Re-verify ownership before deleting.
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    changed = await db.delete_task(callback.from_user.id, task_id)
    if changed:
        await forwarding.remove_task(task_id)
    if callback.message:
        await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer("Deleted" if changed else "Not found")


@router.callback_query(F.data.startswith("task:edit-source:"))
async def edit_source_cb(
    callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService,
) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    await state.set_state(TaskStates.waiting_source)
    existing = _json_field(task["sources"], [])
    await state.update_data(
        edit_task_id=task_id, edit_field="sources", sources=list(existing), picker_dialogs=None,
    )
    language = await _language_for_callback(db, callback)
    await _render_chat_picker(
        callback.message, db, telethon, state, callback.from_user.id, "src", language, edit=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:edit-dest:"))
async def edit_dest_cb(
    callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService,
) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    await state.set_state(TaskStates.waiting_destination)
    existing = _json_field(task["destinations"], [])
    await state.update_data(
        edit_task_id=task_id, edit_field="destinations",
        destinations=list(existing), picker_dialogs=None,
    )
    language = await _language_for_callback(db, callback)
    await _render_chat_picker(
        callback.message, db, telethon, state, callback.from_user.id, "dst", language, edit=True,
    )
    await callback.answer()


# ==========================================
# /config — READ-ONLY CONFIGURATION SUMMARY
# ==========================================
# Deliberately shows ONLY features that actually exist in /settings. Listing a
# toggle here that has no matching control would send users hunting for it.

def _dot(value: bool) -> str:
    return "🟢 ON" if value else "🔴 OFF"


def _filled(value) -> str:
    """Shows how many entries a filter holds, or that it is empty."""
    if isinstance(value, dict):
        return f"✅ [{len(value)} set]" if value else "❌ [Empty]"
    if isinstance(value, (list, tuple)):
        return f"✅ [{len(value)} set]" if value else "❌ [Empty]"
    text = str(value or "").strip()
    return f"✅ [{text[:24]}]" if text else "❌ [Empty]"


def _config_text(task, plan_name: str, language: str) -> str:
    from .forwarding import CODE_FILTER_OFF, code_filter_mode
    from .plans import (
        F_ANTIBAN, F_ATTACH_FILE, F_AUTO_DELETE, F_AUTO_REACTION, F_BLACKLIST,
        F_DELAY_TIMER, F_FOOTER, F_HEADER, F_HIDDEN_LINKS, F_LINK_PREVIEW,
        F_MONO_TEXT, F_POST_EDIT_SYNC, F_REMOVE_LINKS, F_REMOVE_USERNAMES,
        F_REPLACE_LINKS, F_REPLACE_USERNAMES, F_REPLACE_WORDS, F_SENDER_FILTER,
        F_TOPICS, F_TRIM_WORDS, F_WATERMARK_IMAGE, F_WHITELIST, plan_has,
    )

    settings = _json_field(task["settings"], {})
    sources = [s for s in _json_field(task["sources"], []) if isinstance(s, dict)]
    destinations = [d for d in _json_field(task["destinations"], []) if isinstance(d, dict)]

    def line(label: str, ok: bool, feature: str, extra: str = "") -> str:
        if not plan_has(plan_name, feature):
            return f"{label}: 🔒 Locked"
        return f"{label}: {_dot(ok)}{extra}"

    out: list[str] = []
    out.append(f"🛠️ <b>Your Current Configuration for {safe_html(task['task_name'])}</b>")
    out.append("━━━━━━━━━━━━━━━━━━━")
    out.append("")

    out.append("📥 <b>Source Channels for Copy Post</b>")
    if sources:
        for s in sources:
            out.append(f"   └─ • {safe_html(s.get('title') or s.get('username') or s.get('id'))}")
    else:
        out.append("   └─ • ❌ No Source Channels Configured.")
    out.append("")

    out.append("🎯 <b>Target Channels for Forwarding</b>")
    if destinations:
        for d in destinations:
            out.append(f"   └─ • {safe_html(d.get('title') or d.get('username') or d.get('id'))}")
    else:
        out.append("   └─ • ❌ No Target Channels Configured.")
    out.append("")

    code_mode = code_filter_mode(settings)
    code_label = {
        "mono": " [Monospace]", "spoiler": " [Spoiler]", "both": " [Mono + Spoiler]",
    }.get(code_mode, "")
    try:
        auto_delete = int(settings.get("auto_delete_seconds") or 0)
    except (TypeError, ValueError):
        auto_delete = 0
    reaction = settings.get("auto_reaction")
    reaction = reaction if isinstance(reaction, dict) else {}
    topics_cfg = settings.get("topics")
    topics_cfg = topics_cfg if isinstance(topics_cfg, dict) else {}
    topic_count = sum(len(v) for v in topics_cfg.values() if isinstance(v, list))

    out.append("⚙️ <b>General Settings</b>")
    out.append(f"  ┌─ Forwarding Status: {_dot(not task['is_paused'])}")
    out.append(f"  ├─ {line('Header', bool(settings.get('header')), F_HEADER)}")
    out.append(f"  ├─ {line('Footer', bool(settings.get('footer')), F_FOOTER)}")
    out.append(f"  ├─ Media Forwarding: {_dot(code_mode == CODE_FILTER_OFF)}"
               + ("" if code_mode == CODE_FILTER_OFF else " [Off — Code Filter active]"))
    out.append(f"  ├─ {line('URL Preview', bool(settings.get('link_preview', True)), F_LINK_PREVIEW)}")
    out.append(f"  ├─ {line('Remove Links', bool(settings.get('remove_links')), F_REMOVE_LINKS)}")
    out.append(f"  ├─ {line('Remove Usernames', bool(settings.get('remove_usernames')), F_REMOVE_USERNAMES)}")
    out.append(f"  ├─ {line('Disable Hidden Links', bool(settings.get('disable_hidden_links')), F_HIDDEN_LINKS)}")
    out.append(f"  ├─ {line('Auto Delete Messages', auto_delete > 0, F_AUTO_DELETE, f' [{auto_delete}s]' if auto_delete else '')}")
    out.append(f"  ├─ {line('Post Edit Sync', bool(settings.get('post_edit_sync')), F_POST_EDIT_SYNC)}")
    out.append(f"  ├─ {line('Code Filter', code_mode != CODE_FILTER_OFF, F_MONO_TEXT, code_label)}")
    out.append(f"  ├─ {line('Topics Forwarding', topic_count > 0, F_TOPICS, f' [{topic_count} topics]' if topic_count else ' [All topics]')}")
    out.append(f"  ├─ {line('Image Watermark', bool(settings.get('watermark')), F_WATERMARK_IMAGE)}")
    out.append(f"  ├─ {line('Attach Custom File', bool(settings.get('attach_stored_file', False)), F_ATTACH_FILE)}")
    out.append(f"  └─ {line('Auto Reaction', bool(reaction.get('enabled')), F_AUTO_REACTION, f" [{reaction.get('emoji')}]" if reaction.get('enabled') else '')}")
    out.append("")

    def frow(mark: str, label: str, key: str, feature: str) -> str:
        if not plan_has(plan_name, feature):
            return f"{mark} {label}: 🔒 Locked"
        return f"{mark} {label}: {_filled(settings.get(key))}"

    delay = settings.get("delay_timer") or "off"
    antiban = settings.get("antiban_speed") or "off"

    out.append("🧹 <b>Filters &amp; Replacements</b>")
    out.append(f"  ┌─ {frow('🚫', 'Blacklist Keywords', 'blacklist', F_BLACKLIST)}")
    out.append(f"  ├─ {frow('✅', 'Whitelist Keywords', 'whitelist', F_WHITELIST)}")
    out.append(f"  ├─ {frow('👤', 'Sender Filter', 'user_filter', F_SENDER_FILTER)}")
    out.append(f"  ├─ {frow('✨', 'Trim Words', 'trim_words', F_TRIM_WORDS)}")
    out.append(f"  ├─ {frow('🔗', 'Replace Links', 'replace_links', F_REPLACE_LINKS)}")
    out.append(f"  ├─ {frow('👥', 'Replace Usernames', 'replace_usernames', F_REPLACE_USERNAMES)}")
    out.append(f"  ├─ {frow('📝', 'Replace Words', 'replace_words', F_REPLACE_WORDS)}")
    out.append(f"  ├─ {frow('🔼', 'Add Header', 'header', F_HEADER)}")
    out.append(f"  ├─ {frow('🔽', 'Add Footer', 'footer', F_FOOTER)}")
    out.append(f"  ├─ ⏳ Target Delay Timer: [{str(delay).title()}]"
               if plan_has(plan_name, F_DELAY_TIMER) else "  ├─ ⏳ Target Delay Timer: 🔒 Locked")
    out.append(f"  └─ 🛡️ Anti-Ban Speed: [{str(antiban).title()}]"
               if plan_has(plan_name, F_ANTIBAN) else "  └─ 🛡️ Anti-Ban Speed: 🔒 Locked")
    out.append("━━━━━━━━━━━━━━━━━━━")
    out.append(safe_t(language, "config_footer"))
    return "\n".join(out)


async def _config_task_picker(db: Database, user_id: int) -> InlineKeyboardMarkup | None:
    tasks = await db.list_tasks(user_id)
    if not tasks:
        return None
    rows = [
        [InlineKeyboardButton(text=f"🛠️ {t_['task_name'][:28]}", callback_data=f"cfg:show:{t_['id']}")]
        for t_ in tasks
    ]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("config"))
async def config_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    markup = await _config_task_picker(db, message.from_user.id)
    if markup is None:
        return await message.answer(
            safe_t(language, "config_no_tasks"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Create Task", callback_data="task:create")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
        )
    await message.answer(safe_t(language, "config_select_task"), reply_markup=markup)


@router.callback_query(F.data.startswith("cfg:show:"))
async def config_show_cb(callback: CallbackQuery, db: Database) -> None:
    """Read-only view. Nothing here changes a setting — that is what /settings
    is for, and the footer says so."""
    if callback.message is None:
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    user = await db.get_user(callback.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    language = language_for(user["preferred_language"]) if user else "en"
    await _safe_edit(
        callback.message,
        _config_text(task, plan_name, language),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Change Settings", callback_data=f"st:task:{task_id}")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="cfg:list")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "cfg:list")
async def config_list_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    markup = await _config_task_picker(db, callback.from_user.id)
    if markup is None:
        await _safe_edit(callback.message, safe_t(language, "config_no_tasks"), _nav_keyboard())
        return await callback.answer()
    await _safe_edit(callback.message, safe_t(language, "config_select_task"), markup)
    await callback.answer()


# ==========================================
# WITHDRAWALS (self-serve payouts)
# ==========================================

PAYOUT_METHODS = {
    "upi": "🇮🇳 UPI",
    "usdt": "🪙 USDT TRC20",
    "stars": "⭐ Telegram Stars",
    "wallet": "💬 Telegram Wallet",
}

PAYOUT_PROMPTS = {
    "upi": "Send your UPI ID\n\nExample: <code>himanshu@paytm</code>",
    "usdt": "Send your USDT TRC20 wallet address\n\nExample: <code>TXYZ...abc</code>",
    "stars": "Send the @username Stars should be sent to\n\nExample: <code>@himanshu</code>",
    "wallet": "Send the @username linked to your Telegram Wallet\n\nExample: <code>@himanshu</code>",
}


async def _withdraw_screen(db: Database, user_id: int):
    summary = await db.referral_summary(user_id)
    balance = summary["unpaid_paise"]
    method, address = await db.get_payout_method(user_id)
    pending = await db.pending_withdrawal(user_id)

    if pending is not None:
        text = (
            "💸 <b>Withdraw Earnings</b>\n\n"
            f"⏳ You already have a payout request pending.\n\n"
            f"Amount: <b>{format_paise(int(pending['amount_paise']))}</b>\n"
            f"Method: {PAYOUT_METHODS.get(str(pending['method']), pending['method'])}\n\n"
            "You'll be notified once it is processed."
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:refer")],
        ])

    if balance < MIN_WITHDRAWAL_PAISE:
        short = MIN_WITHDRAWAL_PAISE - balance
        text = (
            "💸 <b>Withdraw Earnings</b>\n\n"
            f"💰 Available: <b>{format_paise(balance)}</b>\n"
            f"📊 Minimum: {format_paise(MIN_WITHDRAWAL_PAISE)}\n\n"
            f"⚠️ You need <b>{format_paise(short)}</b> more to withdraw."
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Share Your Link", callback_data="menu:refer")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:refer")],
        ])

    if not method or not address:
        text = (
            "💸 <b>Withdraw Earnings</b>\n\n"
            f"💰 Available: <b>{format_paise(balance)}</b>\n\n"
            "🏦 You haven't set a payment method yet.\n"
            "Set one to request your payout."
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏦 Set Payment Method", callback_data="pm:menu")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:refer")],
        ])

    text = (
        "💸 <b>Withdraw Earnings</b>\n\n"
        f"💰 Available: <b>{format_paise(balance)}</b>\n"
        f"🏦 Method: {PAYOUT_METHODS.get(method, method)} · <code>{safe_html(address)}</code>\n\n"
        f"Request payout of <b>{format_paise(balance)}</b>?"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Request Payout", callback_data="wd:request")],
        [InlineKeyboardButton(text="🏦 Change Method", callback_data="pm:menu")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="menu:refer")],
    ])


@router.callback_query(F.data == "wd:menu")
async def withdraw_menu_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    text, markup = await _withdraw_screen(db, callback.from_user.id)
    await _safe_edit(callback.message, text, markup)
    await callback.answer()


@router.callback_query(F.data == "wd:request")
async def withdraw_request_cb(
    callback: CallbackQuery, db: Database, settings: Settings,
) -> None:
    if callback.message is None:
        return
    # Re-read the balance at submit time: it may have changed since the screen
    # was drawn, and we must never file a request for money that isn't there.
    summary = await db.referral_summary(callback.from_user.id)
    balance = summary["unpaid_paise"]
    method, address = await db.get_payout_method(callback.from_user.id)

    if balance < MIN_WITHDRAWAL_PAISE:
        return await callback.answer(
            f"Minimum is {format_paise(MIN_WITHDRAWAL_PAISE)}. Your balance: {format_paise(balance)}",
            show_alert=True,
        )
    if not method or not address:
        return await callback.answer("Set a payment method first", show_alert=True)

    request_id = await db.create_withdrawal(callback.from_user.id, balance, method, address)
    if request_id is None:
        return await callback.answer("You already have a pending request", show_alert=True)

    user = await db.get_user(callback.from_user.id)
    await _notify_admins(
        callback.bot, settings,
        f"💸 <b>Payout Request</b>\n\n"
        f"User: {_format_name(user)} (<code>{callback.from_user.id}</code>)\n"
        f"Amount: <b>{format_paise(balance)}</b>\n"
        f"Method: {PAYOUT_METHODS.get(method, method)}\n"
        f"Address: <code>{safe_html(address)}</code>\n\n"
        f"Review with /withdrawals",
    )
    await _safe_edit(
        callback.message,
        "✅ <b>Payout Requested</b>\n\n"
        f"Amount: <b>{format_paise(balance)}</b>\n"
        f"Method: {PAYOUT_METHODS.get(method, method)}\n\n"
        "You'll be notified once it is processed.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer("Requested")


# ==========================================
# PAYMENT METHOD
# ==========================================

@router.callback_query(F.data == "pm:menu")
async def payout_method_menu(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    method, address = await db.get_payout_method(callback.from_user.id)
    current = PAYOUT_METHODS.get(method, "❌ Not set") if method else "❌ Not set"
    rows = [[InlineKeyboardButton(text=label, callback_data=f"pm:set:{key}")]
            for key, label in PAYOUT_METHODS.items()]
    rows.append([InlineKeyboardButton(text="◀️ Back", callback_data="wd:menu")])
    await _safe_edit(
        callback.message,
        "🏦 <b>Payment Method</b>\n\n"
        f"Current: <b>{current}</b>\n"
        f"Address: <code>{safe_html(address) if address else 'Not set'}</code>\n\n"
        "Select a method to update:",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pm:set:"))
async def payout_method_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    method = callback.data.rsplit(":", 1)[1]
    if method not in PAYOUT_METHODS:
        return await callback.answer("Invalid method", show_alert=True)
    await state.set_state(PayoutStates.waiting_address)
    await state.update_data(payout_method=method)
    await _safe_edit(
        callback.message,
        f"🏦 <b>{PAYOUT_METHODS[method]}</b>\n\n{PAYOUT_PROMPTS[method]}\n\nSend /back to cancel.",
        None,
    )
    await callback.answer()


@router.message(PayoutStates.waiting_address)
async def payout_method_save(message: Message, state: FSMContext, db: Database) -> None:
    if not message.text:
        return
    raw = message.text.strip()
    if raw == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    if len(raw) < 3 or len(raw) > 200:
        return await message.answer("⚠️ That doesn't look right. Please send a valid address.")

    data = await state.get_data()
    method = str(data.get("payout_method", ""))
    if method not in PAYOUT_METHODS:
        await state.clear()
        return await message.answer("⚠️ Something went wrong, try again from Refer & Earn.")

    await db.set_payout_method(message.from_user.id, method, raw)
    await state.clear()
    await message.answer(
        f"✅ <b>Payment method saved</b>\n\n"
        f"Method: {PAYOUT_METHODS[method]}\n"
        f"Address: <code>{safe_html(raw)}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💸 Withdraw", callback_data="wd:menu")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )


# ==========================================
# ADMIN: WITHDRAWAL REVIEW
# ==========================================

@router.message(Command("withdrawals"))
async def withdrawals_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    rows = await db.list_pending_withdrawals()
    if not rows:
        return await message.answer("✅ No pending payout requests.")
    for w in rows:
        label = safe_html(w["first_name"] or w["username"] or w["user_id"])
        await message.answer(
            f"💸 <b>Payout Request #{w['id']}</b>\n\n"
            f"User: {label} (<code>{w['user_id']}</code>)\n"
            f"Amount: <b>{format_paise(int(w['amount_paise']))}</b>\n"
            f"Method: {PAYOUT_METHODS.get(str(w['method']), w['method'])}\n"
            f"Address: <code>{safe_html(w['address'])}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Mark Paid", callback_data=f"wdr:ok:{w['id']}"),
                InlineKeyboardButton(text="❌ Reject", callback_data=f"wdr:no:{w['id']}"),
            ]]),
        )


@router.callback_query(F.data.startswith("wdr:"))
async def withdrawal_review_cb(
    callback: CallbackQuery, db: Database, settings: Settings,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    _, action, rid = callback.data.split(":")
    request_id = int(rid)

    request = await db.get_withdrawal(request_id)
    if request is None:
        return await callback.answer("Not found", show_alert=True)
    if str(request["status"]) != "pending":
        return await callback.answer("Already handled", show_alert=True)

    user_id = int(request["user_id"])
    amount = int(request["amount_paise"])

    # Claim the row first — if this fails another admin got there already and
    # we must not touch the balance.
    status = "paid" if action == "ok" else "rejected"
    if not await db.set_withdrawal_status(request_id, status, callback.from_user.id):
        return await callback.answer("Already handled", show_alert=True)

    note = ""
    if action == "ok":
        # Only now are the commissions marked paid, so a rejected request
        # leaves the user's balance untouched.
        paid = await db.payout_referrals(user_id)
        note = f"✅ Marked paid — {format_paise(paid or amount)}"
        with suppress(Exception):
            await callback.bot.send_message(
                user_id,
                f"💸 <b>Payout sent!</b>\n\nAmount: <b>{format_paise(paid or amount)}</b>\n"
                f"Method: {PAYOUT_METHODS.get(str(request['method']), request['method'])}",
                parse_mode="HTML",
            )
    else:
        note = "❌ Rejected — balance kept"
        with suppress(Exception):
            await callback.bot.send_message(
                user_id,
                "❌ <b>Payout request rejected</b>\n\n"
                "Your balance has not been touched. Contact support for details.",
                parse_mode="HTML",
            )

    if callback.message:
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(
                (callback.message.html_text or callback.message.text or "") + f"\n\n{note}",
                parse_mode="HTML",
            )
    await callback.answer(note[:60])


# ==========================================
# ADMIN
# ==========================================

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Stats", callback_data="admin:stats"),
         InlineKeyboardButton(text="📣 Broadcast", callback_data="admin:broadcast:start")],
        [InlineKeyboardButton(text="📅 Weekly Report", callback_data="admin:weekly"),
         InlineKeyboardButton(text="👥 Recent Users", callback_data="admin:users")],
        [InlineKeyboardButton(text="👤 User Info", callback_data="admin:userinfo:start"),
         InlineKeyboardButton(text="🎁 Grant Days", callback_data="admin:grantpicker")],
        [InlineKeyboardButton(text="🏠 User Menu", callback_data="menu:home")],
    ])


async def _resolve_target_user(db: Database, raw: str) -> int | None:
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    user = await db.get_user_by_username(raw)
    return int(user["telegram_user_id"]) if user is not None else None


async def _weekly_report(db: Database) -> str:
    stats = await db.stats()
    return (
        f"📅 <b>Weekly Report</b>\n\n"
        f"Users: {stats.get('users', 0)}\n"
        f"New today: {stats.get('new_users_today', 0)}\n"
        f"Paid: {stats.get('paid_users', 0)}\n"
        f"Active tasks: {stats.get('active_tasks', 0)}\n"
        f"Captured payments: {stats.get('captured_payments', 0)}\n"
        f"Pending manual payments: {stats.get('pending_manual_payments', 0)}"
    )


@router.message(Command("admin"))
async def admin_dashboard(message: Message, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    await message.answer("🛠️ <b>Admin Dashboard</b>", parse_mode="HTML", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message:
        await _safe_edit(callback.message, "🛠️ <b>Admin Dashboard</b>", admin_keyboard())
    await callback.answer()


@router.message(Command("stats"))
async def stats_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    stats = await db.stats()
    await message.answer(
        "📊 <b>Admin Stats</b>\n\n"
        + "\n".join(f"<b>{k.replace('_', ' ').title()}:</b> {v}" for k, v in stats.items()),
        parse_mode="HTML", reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data == "admin:stats")
async def admin_stats_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    stats = await db.stats()
    await _safe_edit(
        callback.message,
        "📊 <b>Stats</b>\n\n"
        + "\n".join(f"<b>{k.replace('_', ' ').title()}:</b> {v}" for k, v in stats.items()),
        admin_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:weekly")
async def weekly_report_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    await _safe_edit(callback.message, await _weekly_report(db), admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def recent_users_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    users = await db.list_recent_active_users(6)
    lines = ["👥 <b>Recent Users</b>\n"]
    for u in users:
        lines.append(
            f"{u['telegram_user_id']} — "
            f"{safe_html(u['first_name'] or u['username'] or 'No name')} — "
            f"{str(u['plan']).title()}"
        )
    await _safe_edit(callback.message, "\n".join(lines), admin_keyboard())
    await callback.answer()


async def _full_user_info_card(db: Database, u) -> tuple[str, InlineKeyboardMarkup]:
    label = safe_html(u["first_name"] or u["username"] or "No name")
    block_label = "✅ Unblock" if u["is_blocked"] else "⛔ Block"
    block_action = "unblock" if u["is_blocked"] else "block"
    task_count = await db.count_tasks(int(u["telegram_user_id"]))
    connected = await db.has_active_session(int(u["telegram_user_id"]))
    text = (
        f"👤 <b>{label}</b>\n"
        f"Username: @{safe_html(u['username'] or '—')}\n"
        f"Telegram ID: <code>{u['telegram_user_id']}</code>\n"
        f"Language: {safe_html(u['preferred_language'] or '—')}\n"
        f"Membership: {'✅ Joined' if u['updates_channel_member'] else '❌ Not joined'}\n"
        f"Telegram account: {'✅ Connected' if connected else '❌ Not connected'}\n\n"
        f"💎 <b>Subscription</b>\n"
        f"Plan: {safe_html(str(u['plan']).title())}\n"
        f"Plan expiry: {u['plan_expiry'] or '—'}\n"
        f"Scheduled plan: "
        f"{safe_html(str(u['scheduled_plan']).title()) if u['scheduled_plan'] else '—'}\n\n"
        f"📋 Tasks: {task_count}\n"
        f"Blocked: {'Yes' if u['is_blocked'] else 'No'}\n"
        f"Joined bot: {u['created_at']}\n"
        f"Last active: {u['last_seen_at']}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Grant Days", callback_data=f"admin:grant:{u['telegram_user_id']}"),
         InlineKeyboardButton(text=block_label, callback_data=f"admin:{block_action}:{u['telegram_user_id']}")],
        [InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")],
    ])
    return text, keyboard


async def _recent_users_picker(message: Message, db: Database, action: str, title: str) -> None:
    users = await db.list_recent_active_users(6)
    if not users:
        return await message.answer("No users found.")
    rows = []
    for u in users:
        label = safe_html(u["first_name"] or u["username"] or str(u["telegram_user_id"]))
        rows.append([InlineKeyboardButton(
            text=f"👤 {label} ({u['plan']})",
            callback_data=f"admin:{action}:{u['telegram_user_id']}",
        )])
    rows.append([InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")])
    await message.answer(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


async def _recent_users_picker_edit(
    callback: CallbackQuery, db: Database, action: str, title: str,
) -> None:
    if callback.message is None:
        return
    users = await db.list_recent_active_users(6)
    if not users:
        await _safe_edit(callback.message, "No users found.", admin_keyboard())
        return await callback.answer()
    rows = []
    for u in users:
        label = safe_html(u["first_name"] or u["username"] or str(u["telegram_user_id"]))
        rows.append([InlineKeyboardButton(
            text=f"👤 {label} ({u['plan']})",
            callback_data=f"admin:{action}:{u['telegram_user_id']}",
        )])
    rows.append([InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")])
    await _safe_edit(callback.message, title, InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data == "admin:userinfo:start")
async def admin_userinfo_picker_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    await _render_user_picker(callback.message, db, "uinfo", ADMIN_PICKER_TITLES["uinfo"], 0)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:uinfo:"))
async def admin_userinfo_show_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    target_user_id = int(callback.data.rsplit(":", 1)[1])
    user = await db.get_user(target_user_id)
    if not user:
        await _safe_edit(callback.message, "⚠️ Not found.", admin_keyboard())
        return await callback.answer()
    text, keyboard = await _full_user_info_card(db, user)
    await _safe_edit(callback.message, text, keyboard)
    await callback.answer()


@router.message(Command("userinfo"))
async def user_info_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) == 1:
        return await _render_user_picker(message, db, "uinfo", ADMIN_PICKER_TITLES["uinfo"], 0)
    if len(parts) != 2:
        return await message.answer(
            "Usage: /userinfo &lt;user&gt;\nOr send /userinfo with no arguments to pick from buttons.",
            parse_mode="HTML",
        )
    user_id = await _resolve_target_user(db, parts[1])
    user = await db.get_user(user_id) if user_id else None
    if not user:
        return await message.answer("⚠️ Not found.")
    text, keyboard = await _full_user_info_card(db, user)
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("listusers"))
async def list_users_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    users = await db.list_recent_active_users(6)
    if not users:
        return await message.answer("No users found.")
    for u in users:
        text, keyboard = await _full_user_info_card(db, u)
        await message.answer(text, reply_markup=keyboard)


@router.message(Command("block", "unblock"))
async def block_user_command(
    message: Message, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) == 1:
        return await _render_user_picker(message, db, "block", ADMIN_PICKER_TITLES["block"], 0)
    if len(parts) != 2:
        return await message.answer(
            "Usage: /block &lt;telegram_user_id or @username&gt;\n"
            "Or send /block with no arguments to pick from buttons.",
            parse_mode="HTML",
        )
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        return await message.answer("⚠️ User not found.")
    blocked = parts[0].lower().startswith("/block")
    changed = await db.set_blocked(user_id, blocked)
    if blocked:
        await forwarding.remove_user(user_id)
    else:
        await forwarding.refresh_user(user_id)
    await message.answer("✅ Updated." if changed else "⚠️ Not found.")


@router.callback_query(F.data.startswith("admin:block:") | F.data.startswith("admin:unblock:"))
async def admin_block_toggle(
    callback: CallbackQuery, db: Database, forwarding: ForwardingEngine, settings: Settings,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    parts = callback.data.split(":")
    action, target_user_id = parts[1], int(parts[2])
    blocked = action == "block"
    changed = await db.set_blocked(target_user_id, blocked)
    if blocked:
        await forwarding.remove_user(target_user_id)
    else:
        await forwarding.refresh_user(target_user_id)
    await callback.answer("Updated" if changed else "Not found", show_alert=True)


@router.callback_query(F.data == "admin:grantpicker")
async def admin_grant_picker_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    await _render_user_picker(callback.message, db, "grant", ADMIN_PICKER_TITLES["grant"], 0)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:grant:"))
async def admin_grant_pick_plan(callback: CallbackQuery, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    target_user_id = int(callback.data.rsplit(":", 1)[1])
    await _safe_edit(
        callback.message,
        f"🎁 Grant which plan to <code>{target_user_id}</code>?",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=plan.name, callback_data=f"admin:grantplan:{target_user_id}:{key}")
             for key, plan in PLANS.items() if key != "free"],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:grantplan:"))
async def admin_grant_pick_days(
    callback: CallbackQuery, settings: Settings, state: FSMContext,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    _, _, target_user_id, plan_key = callback.data.split(":")
    await state.set_state(AdminStates.waiting_grant_days)
    await state.update_data(target_user_id=int(target_user_id), plan=plan_key)
    await _safe_edit(callback.message, "📅 How many days? (Send a number)", None)
    await callback.answer()


@router.message(AdminStates.waiting_grant_days)
async def admin_grant_days_finish(
    message: Message, state: FSMContext, db: Database, settings: Settings,
    forwarding: ForwardingEngine,
) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    if not message.text or not message.text.strip().isdigit():
        return await message.answer("⚠️ Send a valid number.")
    data = await state.get_data()
    target_user_id = int(data["target_user_id"])
    changed = await db.set_plan(target_user_id, str(data["plan"]), int(message.text.strip()))
    await state.clear()
    if changed:
        with suppress(Exception):
            await forwarding.refresh_user(target_user_id)
    await message.answer("✅ Granted." if changed else "⚠️ Not found.", reply_markup=admin_keyboard())


@router.message(Command("grantdays"))
async def grant_days_command(
    message: Message, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) == 1:
        return await _render_user_picker(message, db, "grant", ADMIN_PICKER_TITLES["grant"], 0)
    if len(parts) not in (3, 4) or not parts[2].isdigit():
        return await message.answer(
            "Usage: /grantdays &lt;user&gt; &lt;days&gt; [plan]\n"
            "Or send /grantdays with no arguments to pick from buttons.",
            parse_mode="HTML",
        )
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        return await message.answer("⚠️ User not found.")
    if len(parts) == 4:
        plan_key = parts[3].lower()
        if plan_key not in PLANS or plan_key == "free":
            return await message.answer("⚠️ Invalid premium plan.")
    else:
        target = await db.get_user(user_id)
        plan_key = str(target["plan"]) if target and target["plan"] != "free" else "silver"
    changed = await db.set_plan(user_id, plan_key, int(parts[2]))
    if changed:
        with suppress(Exception):
            await forwarding.refresh_user(user_id)
    await message.answer(f"✅ {plan_key} granted." if changed else "⚠️ Not found.")


# ==========================================
# ADMIN USER PICKER (numbered list, paginated)
# ==========================================
# Same shape as the source/destination picker: numbered buttons so nothing has
# to be typed. Ordered by most recently active, 10 per page.

ADMIN_PAGE_SIZE = 10


async def _render_user_picker(
    message_obj, db: Database, action: str, title: str, page: int = 0,
) -> None:
    total = await db.count_all_users()
    pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    users = await db.list_users_page(page * ADMIN_PAGE_SIZE, ADMIN_PAGE_SIZE)

    if not users:
        text, markup = "No users found.", admin_keyboard()
    else:
        lines = [title, ""]
        number_row: list[InlineKeyboardButton] = []
        rows: list[list[InlineKeyboardButton]] = []
        for idx, u in enumerate(users):
            number = idx + 1
            uid = int(u["telegram_user_id"])
            label = safe_html(u["first_name"] or u["username"] or uid)[:26]
            plan = str(u["plan"] or "free").title()
            flag = " ⛔" if u["is_blocked"] else ""
            lines.append(f"{number}. {label} — {plan}{flag}")
            number_row.append(InlineKeyboardButton(
                text=str(number), callback_data=f"apick:{action}:{page}:{uid}",
            ))
            if len(number_row) == 5:
                rows.append(number_row)
                number_row = []
        if number_row:
            rows.append(number_row)

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"apage:{action}:{page - 1}"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"apage:{action}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")])

        lines.append("")
        lines.append(f"Page {page + 1} of {pages} · {total} users total")
        lines.append("👆 Tap a number to select that user")
        text, markup = "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)

    if hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        with suppress(TelegramBadRequest):
            await message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
    await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")


ADMIN_PICKER_TITLES = {
    "grant": "🎁 <b>Grant days — select a user</b>",
    "uinfo": "👤 <b>User info — select a user</b>",
    "block": "⛔ <b>Block / unblock — select a user</b>",
    "payout": "💰 <b>Referral payout — select a user</b>",
}


@router.callback_query(F.data.startswith("apage:"))
async def admin_picker_page_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    _, action, page = callback.data.split(":")
    await _render_user_picker(
        callback.message, db, action,
        ADMIN_PICKER_TITLES.get(action, "👥 <b>Select a user</b>"), int(page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("apick:"))
async def admin_picker_select_cb(
    callback: CallbackQuery, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    _, action, page, uid_str = callback.data.split(":")
    target_user_id, page = int(uid_str), int(page)

    user = await db.get_user(target_user_id)
    if user is None:
        return await callback.answer("User not found", show_alert=True)
    label = safe_html(user["first_name"] or user["username"] or target_user_id)

    if action == "uinfo":
        text, keyboard = await _full_user_info_card(db, user)
        await _safe_edit(callback.message, text, keyboard)
        return await callback.answer()

    if action == "block":
        blocked = not user["is_blocked"]
        await db.set_blocked(target_user_id, blocked)
        if blocked:
            await forwarding.remove_user(target_user_id)
        else:
            await forwarding.refresh_user(target_user_id)
        await _render_user_picker(
            callback.message, db, "block", ADMIN_PICKER_TITLES["block"], page,
        )
        return await callback.answer(f"{label} {'blocked' if blocked else 'unblocked'}")

    if action == "payout":
        total = await db.payout_referrals(target_user_id)
        if total <= 0:
            return await callback.answer("Nothing owed to that user", show_alert=True)
        with suppress(Exception):
            await callback.bot.send_message(
                target_user_id,
                f"💸 <b>Referral payout sent!</b>\n\nAmount: <b>{format_paise(total)}</b>",
                parse_mode="HTML",
            )
        await callback.answer(f"Paid {format_paise(total)}", show_alert=True)
        return await _render_user_picker(
            callback.message, db, "payout", ADMIN_PICKER_TITLES["payout"], page,
        )

    if action == "grant":
        current = str(user["plan"] or "free").title()
        expiry = user["plan_expiry"].astimezone(IST).strftime("%d %b %Y") if user["plan_expiry"] else "—"
        rows = [[InlineKeyboardButton(text=f"💎 {plan.name}", callback_data=f"agrant:{target_user_id}:{key}")]
                for key, plan in PLANS.items() if key != "free"]
        rows.append([InlineKeyboardButton(text="◀️ Back", callback_data=f"apage:grant:{page}")])
        await _safe_edit(
            callback.message,
            f"🎁 <b>Grant plan to {label}</b>\n"
            f"ID: <code>{target_user_id}</code>\n"
            f"Current: {current} (expires {expiry})\n\n"
            f"Which plan?",
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return await callback.answer()

    await callback.answer()


@router.callback_query(F.data.startswith("agrant:"))
async def admin_grant_days_cb(callback: CallbackQuery, settings: Settings) -> None:
    """Day presets, so the common cases need no typing at all."""
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    _, uid_str, plan_key = callback.data.split(":")
    if plan_key not in PLANS or plan_key == "free":
        return await callback.answer("Invalid plan", show_alert=True)
    rows = [
        [InlineKeyboardButton(text="7 days", callback_data=f"agdays:{uid_str}:{plan_key}:7"),
         InlineKeyboardButton(text="30 days", callback_data=f"agdays:{uid_str}:{plan_key}:30")],
        [InlineKeyboardButton(text="90 days", callback_data=f"agdays:{uid_str}:{plan_key}:90"),
         InlineKeyboardButton(text="365 days", callback_data=f"agdays:{uid_str}:{plan_key}:365")],
        # Presets cover the common cases; custom handles the 1-2 day comps.
        [InlineKeyboardButton(text="✍️ Custom days", callback_data=f"agcust:{uid_str}:{plan_key}")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="apage:grant:0")],
    ]
    await _safe_edit(
        callback.message,
        f"🎁 Granting <b>{PLANS[plan_key].name}</b> to <code>{uid_str}</code>\n\nFor how long?",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("agcust:"))
async def admin_grant_custom_cb(
    callback: CallbackQuery, state: FSMContext, settings: Settings,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    _, uid_str, plan_key = callback.data.split(":")
    if plan_key not in PLANS or plan_key == "free":
        return await callback.answer("Invalid plan", show_alert=True)
    await state.set_state(AdminGrantStates.waiting_custom_days)
    await state.update_data(grant_user_id=int(uid_str), grant_plan=plan_key)
    await _safe_edit(
        callback.message,
        f"✍️ <b>Enter number of days</b>\n\n"
        f"Granting <b>{PLANS[plan_key].name}</b> to <code>{uid_str}</code>\n\n"
        f"Example: <code>2</code>\n\nSend /back to cancel.",
        None,
    )
    await callback.answer()


@router.message(AdminGrantStates.waiting_custom_days)
async def admin_grant_custom_apply(
    message: Message, state: FSMContext, db: Database, settings: Settings,
    forwarding: ForwardingEngine,
) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    if not message.text:
        return
    raw = message.text.strip()
    if raw == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=admin_keyboard())
    if not raw.isdigit() or int(raw) <= 0:
        return await message.answer("⚠️ Send a whole number of days, e.g. <code>2</code>", parse_mode="HTML")

    days = int(raw)
    if days > 3650:
        return await message.answer("⚠️ That's over 10 years — send a smaller number.")

    data = await state.get_data()
    target_user_id = int(data.get("grant_user_id", 0))
    plan_key = str(data.get("grant_plan", ""))
    await state.clear()
    if plan_key not in PLANS or plan_key == "free" or not target_user_id:
        return await message.answer("⚠️ Something went wrong, start again from /grantdays")

    if not await db.set_plan(target_user_id, plan_key, days):
        return await message.answer("⚠️ User not found.", reply_markup=admin_keyboard())
    with suppress(Exception):
        await forwarding.refresh_user(target_user_id)

    user = await db.get_user(target_user_id)
    expiry = (
        user["plan_expiry"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        if user and user["plan_expiry"] else "—"
    )
    with suppress(Exception):
        await message.bot.send_message(
            target_user_id,
            f"🎁 <b>Your plan has been upgraded!</b>\n\n"
            f"Plan: <b>{PLANS[plan_key].name}</b>\n"
            f"Days added: {days}\n"
            f"Valid until: {expiry}\n\n"
            f"Use /tasks to get started.",
            parse_mode="HTML",
        )
    await message.answer(
        f"✅ <b>Granted</b>\n\nUser: <code>{target_user_id}</code>\n"
        f"Plan: {PLANS[plan_key].name}\nDays: {days}\nNew expiry: {expiry}",
        reply_markup=admin_keyboard(),
    )


@router.callback_query(F.data.startswith("agdays:"))
async def admin_grant_apply_cb(
    callback: CallbackQuery, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    _, uid_str, plan_key, days_str = callback.data.split(":")
    target_user_id, days = int(uid_str), int(days_str)
    if plan_key not in PLANS or plan_key == "free" or days <= 0:
        return await callback.answer("Invalid option", show_alert=True)

    if not await db.set_plan(target_user_id, plan_key, days):
        return await callback.answer("User not found", show_alert=True)
    with suppress(Exception):
        await forwarding.refresh_user(target_user_id)

    user = await db.get_user(target_user_id)
    expiry = (
        user["plan_expiry"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        if user and user["plan_expiry"] else "—"
    )
    with suppress(Exception):
        await callback.bot.send_message(
            target_user_id,
            f"🎁 <b>Your plan has been upgraded!</b>\n\n"
            f"Plan: <b>{PLANS[plan_key].name}</b>\n"
            f"Days added: {days}\n"
            f"Valid until: {expiry}\n\n"
            f"Use /tasks to get started.",
            parse_mode="HTML",
        )
    await _safe_edit(
        callback.message,
        f"✅ <b>Granted</b>\n\n"
        f"User: <code>{target_user_id}</code>\n"
        f"Plan: {PLANS[plan_key].name}\n"
        f"Days: {days}\n"
        f"New expiry: {expiry}",
        admin_keyboard(),
    )
    await callback.answer("Granted")


@router.message(Command("referralpayout", "payouts"))
async def referral_payout_command(message: Message, db: Database, settings: Settings) -> None:
    """With no argument, shows who is owed money. With a user, pays them out."""
    if not _is_admin(settings, message.from_user.id):
        return
    parts = (message.text or "").split()

    if len(parts) == 1:
        rows = await db.list_pending_payouts()
        if not rows:
            return await message.answer("✅ No pending referral payouts.")
        lines = ["💰 <b>Pending Referral Payouts</b>\n"]
        buttons = []
        for r in rows:
            label = safe_html(r["first_name"] or r["username"] or r["referrer_id"])
            owed = format_paise(int(r["owed"]))
            lines.append(f"{label} (<code>{r['referrer_id']}</code>) — {owed} from {r['refs']} refs")
            buttons.append([InlineKeyboardButton(
                text=f"✅ Pay {label} — {owed}",
                callback_data=f"admin:payout:{r['referrer_id']}",
            )])
        buttons.append([InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")])
        return await message.answer(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        return await message.answer("⚠️ User not found.")
    total = await db.payout_referrals(user_id)
    if total <= 0:
        return await message.answer("⚠️ Nothing owed to that user.")
    with suppress(Exception):
        await message.bot.send_message(
            user_id,
            f"💸 <b>Referral payout sent!</b>\n\nAmount: <b>{format_paise(total)}</b>",
            parse_mode="HTML",
        )
    await message.answer(f"✅ Paid out {format_paise(total)} to <code>{user_id}</code>", parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:payout:"))
async def referral_payout_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    referrer_id = int(callback.data.rsplit(":", 1)[1])
    total = await db.payout_referrals(referrer_id)
    if total <= 0:
        return await callback.answer("Nothing owed (already paid?)", show_alert=True)
    with suppress(Exception):
        await callback.bot.send_message(
            referrer_id,
            f"💸 <b>Referral payout sent!</b>\n\nAmount: <b>{format_paise(total)}</b>",
            parse_mode="HTML",
        )
    if callback.message:
        with suppress(TelegramBadRequest):
            await callback.message.edit_text(
                (callback.message.html_text or callback.message.text or "")
                + f"\n\n✅ Paid {format_paise(total)} to <code>{referrer_id}</code>",
                parse_mode="HTML",
            )
    await callback.answer("Paid")


# ==========================================
# BROADCAST
# ==========================================

@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    await state.set_state(AdminBroadcastStates.waiting_message)
    await message.answer(
        "📣 Send the broadcast message. /back to cancel.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")],
        ]),
    )


@router.message(AdminBroadcastStates.waiting_message)
async def broadcast_message(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    if not message.text:
        return
    if message.text.strip() == "/back":
        await state.clear()
        return await message.answer("Broadcast cancelled.")
    await state.update_data(broadcast_text=message.text[:4000])
    await message.answer(
        "📣 <b>Preview</b>\n\n" + safe_html(message.text[:4000]) + "\n\nChoose an audience:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="All users", callback_data="admin:broadcast:all"),
             InlineKeyboardButton(text="Active users", callback_data="admin:broadcast:active")],
            [InlineKeyboardButton(text="Paid users", callback_data="admin:broadcast:paid"),
             InlineKeyboardButton(text="English", callback_data="admin:broadcast:english")],
            [InlineKeyboardButton(text="Hinglish", callback_data="admin:broadcast:hinglish"),
             InlineKeyboardButton(text="👥 Select Users", callback_data="admin:broadcast:selectusers")],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")],
        ]),
    )


async def _run_broadcast(
    callback: CallbackQuery, state: FSMContext, db: Database, audience: str, users: list,
) -> None:
    data = await state.get_data()
    text = str(data.get("broadcast_text", "")).strip()
    if not text:
        return await callback.answer("Missing text", show_alert=True)
    if callback.message is None:
        return

    broadcast_id = await db.create_broadcast(callback.from_user.id, audience, text, len(users))
    sent = failed = blocked = 0
    with suppress(TelegramBadRequest):
        await callback.message.edit_text(f"📣 Sending to {len(users)} users…", reply_markup=None)

    for i, u in enumerate(users, 1):
        try:
            await callback.bot.send_message(int(u["telegram_user_id"]), text)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
            await db.mark_user_inactive(int(u["telegram_user_id"]))
        except TelegramBadRequest:
            failed += 1
        except Exception:
            failed += 1
        # Telegram rate-limits bulk sends; pausing keeps the whole run alive.
        if i % 20 == 0:
            await asyncio.sleep(1)

    await db.finish_broadcast(broadcast_id, sent, failed, blocked)
    await state.clear()
    with suppress(TelegramBadRequest):
        await callback.message.edit_text(
            f"✅ Broadcast complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
            reply_markup=admin_keyboard(),
        )
    await callback.answer()


async def _render_broadcast_user_picker(
    callback: CallbackQuery, db: Database, state: FSMContext,
) -> None:
    if callback.message is None:
        return
    data = await state.get_data()
    selected: list[int] = list(data.get("broadcast_selected_ids") or [])
    users = await db.list_recent_active_users(6)
    if not users:
        await _safe_edit(callback.message, "No recent active users found.", admin_keyboard())
        return await callback.answer()

    rows = []
    for u in users:
        uid = int(u["telegram_user_id"])
        label = safe_html(u["first_name"] or u["username"] or str(uid))
        mark = "✅ " if uid in selected else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{label} ({u['plan']})", callback_data=f"admin:selu:toggle:{uid}",
        )])
    rows.append([InlineKeyboardButton(
        text=f"✅ Send to {len(selected)} selected", callback_data="admin:selu:done",
    )])
    rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")])
    await _safe_edit(
        callback.message,
        "👥 <b>Select users to broadcast to</b>\n\nTap a name to select or deselect.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:selu:toggle:"))
async def broadcast_select_toggle(
    callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    uid = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    selected: list[int] = list(data.get("broadcast_selected_ids") or [])
    if uid in selected:
        selected.remove(uid)
    else:
        selected.append(uid)
    await state.update_data(broadcast_selected_ids=selected)
    await _render_broadcast_user_picker(callback, db, state)


@router.callback_query(F.data == "admin:selu:done")
async def broadcast_select_done(
    callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    data = await state.get_data()
    selected: list[int] = list(data.get("broadcast_selected_ids") or [])
    if not selected:
        return await callback.answer("Select at least one user first.", show_alert=True)
    users = await db.list_users_by_ids(selected)
    await _run_broadcast(callback, state, db, "selected", users)


@router.callback_query(F.data.startswith("admin:broadcast:"))
async def broadcast_send(
    callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings,
) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None:
        return
    audience = callback.data.rsplit(":", 1)[1]
    if audience == "start":
        await state.set_state(AdminBroadcastStates.waiting_message)
        await _safe_edit(
            callback.message,
            "📣 Send the broadcast message. /back to cancel.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")],
            ]),
        )
        return await callback.answer()
    if audience == "selectusers":
        return await _render_broadcast_user_picker(callback, db, state)
    users = await db.list_broadcast_users(audience)
    await _run_broadcast(callback, state, db, audience, users)


# ==========================================
# FALLBACK — MUST BE THE LAST MESSAGE HANDLER
# ==========================================

@router.message()
async def fallback(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(safe_t(language, "unknown_command"))


def _bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command=cmd.removeprefix("/"), description=desc[:256])
        for cmd, desc, _ in USER_COMMANDS
    ]


def _admin_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command=cmd.removeprefix("/"), description=desc[:256])
        for cmd, desc in ADMIN_COMMANDS
    ]


# ==========================================
# FASTAPI & RAZORPAY WEBHOOK
# ==========================================

def build_app(
    bot: Bot, db: Database, settings: Settings,
    billing: RazorpayBilling, forwarding: ForwardingEngine,
) -> FastAPI:
    app = FastAPI(title="Dealskoti Forwarder", version="1.0.0", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "dealskoti-message-forwarder"}

    @app.post(settings.razorpay_webhook_path)
    async def razorpay_webhook(request: Request) -> JSONResponse:
        signature = request.headers.get("X-Razorpay-Signature", "")
        raw_body = await request.body()
        if not billing.verify_webhook_signature(raw_body, signature):
            await _notify_admins(bot, settings, "🚨 Invalid Razorpay webhook signature rejected")
            return JSONResponse({"error": "Invalid signature"}, status_code=401)

        user_id: int | None = None
        stored_plan = ""
        stored_cycle = ""
        captured = None
        try:
            payload = billing.parse_json(raw_body)
            captured = billing.parse_captured_payment(payload)
            if captured is None:
                return JSONResponse({"status": "ignored"})

            stored_payment = None
            if captured.order_id:
                stored_payment = await db.get_payment_for_order(captured.order_id)

            # `payment.captured` cannot carry the payment-link id, so recover the
            # order from the notes we attached when the link was created.
            if stored_payment is None:
                notes = captured.notes or {}
                raw_uid = str(notes.get("user_id") or "").strip()
                note_plan = str(notes.get("plan") or "").strip().lower()
                note_cycle = str(notes.get("cycle") or "").strip().lower()
                if raw_uid.isdigit() and note_plan and note_cycle:
                    stored_payment = await db.find_pending_payment(int(raw_uid), note_plan, note_cycle)

            if stored_payment is None:
                logger.info("Webhook had no matching pending payment (order_id=%s)", captured.order_id)
                return JSONResponse({"status": "ignored"})

            stored_plan = str(stored_payment["plan"])
            stored_cycle = str(stored_payment["cycle"])

            user_id = await db.activate_payment(
                str(stored_payment["order_id"]), captured.payment_id, captured.amount_paise,
                duration_days(stored_cycle), stored_plan, stored_cycle,
            )
        except (BillingError, ValueError) as exc:
            logger.warning("Rejected webhook: %s", str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("Webhook processing failed")
            return JSONResponse({"error": "internal error"}, status_code=500)

        if user_id is not None and captured is not None:
            # Hot-reload the engine so the new plan's limits apply immediately.
            with suppress(Exception):
                await forwarding.refresh_user(user_id)

            # Referral commission — credited on EVERY payment, so a referrer
            # keeps earning for as long as their referral keeps renewing.
            with suppress(Exception):
                credited = await db.credit_referral_commission(user_id, captured.amount_paise)
                if credited is not None:
                    with suppress(Exception):
                        await bot.send_message(
                            int(credited["referrer_id"]),
                            "🎁 <b>You earned a referral commission!</b>\n\n"
                            f"Unpaid earnings: <b>{format_paise(int(credited['commission_amount_paise']))}</b>\n\n"
                            "Contact support to request a payout.",
                            parse_mode="HTML",
                        )
            user = await db.get_user(user_id)
            if user is not None:
                language = language_for(user["preferred_language"])
                expiry_str = (
                    user["plan_expiry"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
                    if user["plan_expiry"] else "—"
                )
                with suppress(Exception):
                    await bot.send_message(
                        user_id,
                        safe_t(
                            language, "payment_success",
                            plan=PLANS[stored_plan].name if stored_plan in PLANS else stored_plan.title(),
                            days=duration_days(stored_cycle),
                            amount=format_paise(captured.amount_paise),
                            txn_id=captured.payment_id, expiry=expiry_str,
                        ),
                    )
                await _notify_admins(
                    bot, settings,
                    f"✅ <b>Verified Payment</b>\nUser: <code>{user_id}</code>\n"
                    f"Plan: {stored_plan.title()}\n"
                    f"Amount: {format_paise(captured.amount_paise)}",
                )
        return JSONResponse({"status": "processed"})

    return app


# ==========================================
# BACKGROUND MONITORS
# ==========================================

async def _membership_monitor(
    bot: Bot, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    """Pauses forwarding for anyone who leaves the updates channel, and resumes
    it when they rejoin."""
    while True:
        try:
            for row in await db.get_users_for_membership_check():
                user_id = int(row["telegram_user_id"])
                member = await user_is_member(bot, settings, user_id)
                user = await db.get_user(user_id)
                if user is None or bool(user["updates_channel_member"]) == member:
                    continue
                await db.set_membership(user_id, member)
                if member:
                    await db.resume_channel_gate_tasks(user_id)
                    await forwarding.refresh_user(user_id)
                else:
                    await db.mark_channel_gate_paused_tasks(user_id)
                    await forwarding.remove_user(user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Membership monitor iteration failed")
        await asyncio.sleep(300)


async def _send_task_creation_reminders(bot: Bot, db: Database, settings: Settings) -> None:
    """Nudges eligible new users once, 12 hours after signup, if they still
    have no task."""
    for user in await db.list_users_due_task_reminder(12):
        user_id = int(user["telegram_user_id"])
        support = settings.support_bot_link or "/support"
        try:
            await bot.send_message(
                user_id,
                safe_t(
                    language_for(user["preferred_language"]),
                    "task_creation_reminder", support=safe_html(support),
                ),
                parse_mode="HTML",
            )
            await db.mark_task_reminder_sent(user_id)
        except TelegramForbiddenError:
            await db.mark_task_reminder_sent(user_id)
            await db.mark_user_inactive(user_id)
        except Exception:
            logger.exception("Could not send task reminder to user %s", user_id)


# ==========================================
# ENTRYPOINT
# ==========================================

async def _run(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db = Database(settings.database_url)
    await db.connect()

    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    telethon = TelethonService(settings, db)
    billing = RazorpayBilling(settings)

    forwarding = ForwardingEngine(
        db, telethon, settings.max_concurrent_forward_tasks,
        bot_token=settings.telegram_bot_token,
        storage_channel_id=settings.file_storage_channel_id,
        bot=bot,
    )

    dispatcher = Dispatcher()
    # Outer middleware so it runs BEFORE a handler is chosen — otherwise the
    # state-specific handler would win and swallow the command.
    dispatcher.message.outer_middleware(FlowInterruptMiddleware())
    # ORDER MATTERS: the feature routers must come before the main router,
    # whose catch-all message handler would otherwise swallow their input.
    dispatcher.include_router(settings_router)
    dispatcher.include_router(billing_router)
    dispatcher.include_router(router)

    # SAFETY: clear any stale webhook so polling cannot conflict after a restart.
    with suppress(Exception):
        await bot.delete_webhook(drop_pending_updates=True)

    await bot.set_my_commands(_bot_commands())
    for admin_id in settings.admin_telegram_ids:
        with suppress(Exception):
            await bot.set_my_commands(
                _bot_commands() + _admin_bot_commands(),
                scope=BotCommandScopeChat(chat_id=admin_id),
            )

    api = build_app(bot, db, settings, billing, forwarding)
    server = uvicorn.Server(uvicorn.Config(
        api, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), log_level="info",
    ))

    dispatcher_task = asyncio.create_task(dispatcher.start_polling(
        bot, db=db, settings=settings, telethon=telethon,
        billing=billing, forwarding=forwarding,
    ))
    server_task = asyncio.create_task(server.serve())
    membership_task = asyncio.create_task(_membership_monitor(bot, db, settings, forwarding))

    try:
        timezone = ZoneInfo(settings.default_timezone)
    except Exception:
        timezone = ZoneInfo("UTC")
    scheduler = AsyncIOScheduler(timezone=timezone)

    async def send_weekly_report():
        await _notify_admins(bot, settings, await _weekly_report(db))

    async def send_expiry_reminders():
        """Warns users 3 days and then 1 day before expiry.

        The 3-day warning is sent first so a user who is already inside the
        1-day window gets the more urgent one. mark_expiry_reminder_sent()
        records the stage, which is what stops a second copy going out — the
        old version relied on catching a ±1 hour window and silently skipped
        anyone whose expiry fell outside it.
        """
        for stage in (3, 1):
            for row in await db.get_expiring_users(stage):
                user_id = int(row["telegram_user_id"])
                language = language_for(row["preferred_language"])
                plan_label = str(row["plan"]).title()
                try:
                    await bot.send_message(
                        user_id,
                        f"⏳ Your <b>{plan_label}</b> plan expires in {stage} day(s).\n"
                        f"Use /plans to renew.",
                        parse_mode="HTML",
                    )
                    await db.mark_expiry_reminder_sent(user_id, stage)
                except TelegramForbiddenError:
                    await db.mark_expiry_reminder_sent(user_id, stage)
                    await db.mark_user_inactive(user_id)
                except Exception:
                    logger.warning("Could not send expiry reminder to %s", user_id)

    async def downgrade_expired_plans():
        for row in await db.downgrade_expired_users():
            with suppress(Exception):
                await bot.send_message(
                    int(row["telegram_user_id"]),
                    "❌ Your plan has expired and you've been downgraded to Free.\n"
                    "Use /plans to resubscribe.",
                )
            with suppress(Exception):
                await forwarding.refresh_user(int(row["telegram_user_id"]))

    async def send_task_creation_reminders():
        await _send_task_creation_reminders(bot, db, settings)

    async def prune_edit_sync_map():
        """Telegram refuses edits older than 48h, so anything older in the
        sent-message map is dead weight. Without this it grows forever."""
        with suppress(Exception):
            removed = await db.prune_sent_map(older_than_days=3)
            if removed:
                logger.info("Pruned %s stale edit-sync rows", removed)

    scheduler.add_job(send_weekly_report, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=timezone), replace_existing=True)
    scheduler.add_job(send_expiry_reminders, CronTrigger(hour=10, minute=0, timezone=timezone), replace_existing=True)
    scheduler.add_job(downgrade_expired_plans, CronTrigger(hour="*", minute=5, timezone=timezone), replace_existing=True)
    scheduler.add_job(send_task_creation_reminders, CronTrigger(minute=15, timezone=timezone), replace_existing=True)
    scheduler.add_job(prune_edit_sync_map, CronTrigger(hour=4, minute=30, timezone=timezone), replace_existing=True)
    scheduler.start()

    forwarding_task = None
    try:
        async def run_forwarding_engine():
            await forwarding.start()
            await forwarding.run_until_stopped()

        forwarding_task = asyncio.create_task(run_forwarding_engine())
        await asyncio.gather(dispatcher_task, server_task, forwarding_task)
    finally:
        server.should_exit = True
        dispatcher_task.cancel()
        membership_task.cancel()
        scheduler.shutdown(wait=False)
        if forwarding_task is not None:
            forwarding_task.cancel()
        await forwarding.stop()
        await telethon.cancel_all_logins()
        with suppress(asyncio.CancelledError):
            await dispatcher_task
        with suppress(asyncio.CancelledError):
            await membership_task
        if forwarding_task is not None:
            with suppress(asyncio.CancelledError):
                await forwarding_task
        await db.close()
        await bot.session.close()


def run() -> None:
    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc
    asyncio.run(_run(settings))
