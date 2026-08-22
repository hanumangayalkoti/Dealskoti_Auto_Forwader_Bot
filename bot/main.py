from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ErrorEvent,
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .billing import BillingError, RazorpayBilling
from .config import ConfigurationError, Settings
from .db import Database, PLAN_RANKS
from .faq import FAQS
from .forwarding import ForwardingEngine
from .gate import enforce_gate, join_keyboard, user_is_member
from .locales import (
    ADMIN_COMMANDS,
    USER_COMMANDS,
    admin_help,
    command_help,
    language_for,
    t,
)
from .plans import PLANS, duration_days, format_paise, payable_amount_paise, usdt_amount_usd
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti")
router = Router(name="dealskoti")

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

class SettingsFlow(StatesGroup):
    waiting_value = State()

class AdminBroadcastStates(StatesGroup):
    waiting_message = State()

class AdminStates(StatesGroup):
    waiting_grant_days = State()

class UsdtStates(StatesGroup):
    waiting_txid = State()

class UploadStates(StatesGroup):
    waiting_file = State()

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def _is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids

async def _ensure_user(db: Database, message: Message):
    if message.from_user is None:
        raise RuntimeError("Telegram user is missing")
    return await db.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )

async def _language_for_message(db: Database, message: Message):
    user = await _ensure_user(db, message)
    return language_for(user["preferred_language"])

async def _language_for_callback(db: Database, callback: CallbackQuery):
    user = await db.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )
    return language_for(user["preferred_language"])

async def _notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    for admin_id in settings.admin_telegram_ids:
        try:
            await bot.send_message(admin_id, text)
        except (TelegramForbiddenError, TelegramBadRequest):
            logger.warning("Could not notify admin %s", admin_id)

async def _require_connected(db: Database, user_id: int, language: str) -> str | None:
    """Returns None if the user has a connected account (may proceed), otherwise
    returns a 'please connect first' message the caller should show instead of
    the action they attempted. Menus stay browsable without a connection —
    only forwarding-related actions (create task, upload file, buy a plan) call this."""
    if await db.has_active_session(user_id):
        return None
    return safe_t(language, "connect_required")

def _connect_required_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])

def safe_html(text: str) -> str:
    """Safely escape text for HTML parsing to prevent UI breaks."""
    return html.escape(str(text))

def safe_t(lang: str, key: str, **kwargs) -> str:
    try:
        return t(lang, key, **kwargs)
    except Exception:
        logger.warning("Missing translation key %r for language %r", key, lang)
        return f"[{key}]"

async def _menu_text(db: Database, user_id: int, language: str) -> str:
    """Welcome text with the user's name (first_name -> username -> 'User')."""
    user = await db.get_user(user_id)
    name = safe_html(user["first_name"] or user["username"] or "User") if user else "User"
    return safe_t(language, "main_menu", name=name)

# ==========================================
# GLOBAL ERROR HANDLER
# ==========================================

@router.errors()
async def global_error_handler(event: ErrorEvent, settings: Settings) -> bool:
    logger.exception("Unhandled error while processing update", exc_info=event.exception)
    update = event.update
    chat_bot: Bot | None = None
    
    if update.callback_query is not None:
        chat_bot = update.callback_query.bot
        with suppress(Exception):
            await update.callback_query.answer("⚠️ Something went wrong. Please try again.", show_alert=True)
    elif update.message is not None:
        chat_bot = update.message.bot
        with suppress(Exception):
            await update.message.answer("⚠️ Something went wrong processing that. Please try again, or contact support.")
    
    if chat_bot is not None:
        tb = "".join(traceback.format_exception(type(event.exception), event.exception, event.exception.__traceback__))[-3500:]
        with suppress(Exception):
            await _notify_admins(chat_bot, settings, f"🚨 Bot error:\n<pre>{safe_html(tb)}</pre>")
    return True

# ==========================================
# KEYBOARDS
# ==========================================

def _nav_keyboard(*, back: str = "menu:home", include_cancel: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="◀️ Back", callback_data=back)]]
    if include_cancel:
        rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🆓 Free", callback_data="plan:free"),
                InlineKeyboardButton(text="🥈 Silver", callback_data="plan:silver"),
            ],
            [
                InlineKeyboardButton(text="🥇 Gold", callback_data="plan:gold"),
                InlineKeyboardButton(text="💎 Platinum", callback_data="plan:platinum"),
            ],
            [InlineKeyboardButton(text="📊 Compare All Plans", callback_data="plans:compare")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]
    )

def cycles_keyboard(plan: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Weekly", callback_data=f"cycle:{plan}:weekly"),
                InlineKeyboardButton(text="Monthly", callback_data=f"cycle:{plan}:monthly"),
            ],
            [InlineKeyboardButton(text="Yearly — 20% Off", callback_data=f"cycle:{plan}:yearly")],
            [InlineKeyboardButton(text="◀️ Back to Plans", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]
    )

def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇬🇧 English", callback_data="language:en"),
                InlineKeyboardButton(text="🇮🇳 Hinglish", callback_data="language:hinglish"),
            ]
        ]
    )

def main_menu_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect"),
            InlineKeyboardButton(text="👤 My Accounts", callback_data="menu:account"),
        ],
        [
            InlineKeyboardButton(text="➕ New Task", callback_data="task:create"),
            InlineKeyboardButton(text="📋 My Task", callback_data="menu:tasks"),
        ],
        [
            InlineKeyboardButton(text="💎 Plans", callback_data="menu:plans"),
            InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
        ],
        [
            InlineKeyboardButton(text="❓ FAQ", callback_data="faq:page:1"),
            InlineKeyboardButton(text="🌐 Language", callback_data="language:choose"),
        ],
        [
            InlineKeyboardButton(text="📞 Support", url=settings.support_bot_link or "https://t.me/support"),
            InlineKeyboardButton(text="🎁 Refer", callback_data="menu:refer"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def faq_accordion_keyboard(language: str, page: int, expanded_index: int = -1) -> InlineKeyboardMarkup:
    faqs = FAQS[language_for(language)]
    total_pages = max(1, (len(faqs) + 4) // 5)
    page = max(1, min(page, total_pages))
    start = (page - 1) * 5

    rows: list[list[InlineKeyboardButton]] = []
    for index, faq in enumerate(faqs[start : start + 5]):
        actual_index = start + index
        if actual_index == expanded_index:
            continue  # currently open - shown in the message text itself
        q_label = faq.question if len(faq.question) <= 58 else faq.question[:55] + "…"
        rows.append([InlineKeyboardButton(text=f"❓ {q_label}", callback_data=f"faq:item:{page}:{actual_index}")])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"faq:page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"faq:page:{page + 1}"))
    if nav_buttons:
        rows.append(nav_buttons)

    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _faq_view_text(language: str, page: int, expanded_index: int = -1) -> str:
    """Builds the FAQ message text. List view shows a hint; detail view shows Q & A."""
    faqs = FAQS[language_for(language)]
    total_pages = max(1, (len(faqs) + 4) // 5)
    page = max(1, min(page, total_pages))
    header = safe_t(language, "faq_title", page=page, pages=total_pages)
    if expanded_index < 0 or expanded_index >= len(faqs):
        return f"{header}\n\n{safe_t(language, 'faq_hint')}"
    faq = faqs[expanded_index]
    return (
        f"{header}\n\n"
        f"<b>Q</b> - {safe_html(faq.question)}\n\n"
        f"<b>Ans</b> - {safe_html(faq.answer)}"
    )

async def _safe_edit(message_obj, text: str, reply_markup=None) -> None:
    """edit_text that ignores the harmless 'message is not modified' error (double-tap)."""
    try:
        await message_obj.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise

def _plan_features(plan_name: str) -> str:
    plan = PLANS[plan_name]
    daily = f"{plan.daily_messages}/day" if plan.daily_messages else "No normal daily cap"
    return (
        f"Tasks: {plan.tasks}\n"
        f"Sources per task: {plan.sources_per_task}\n"
        f"Destinations per task: {plan.destinations_per_task}\n"
        f"Forwarding: {'Priority' if plan_name in {'gold', 'platinum'} else 'Standard'}\n"
        f"Messages: {daily}"
    )

def _plans_comparison_text(user: dict | None, language: str) -> str:
    """Full side-by-side breakdown of every plan-specific behaviour — not just
    limits, but what actually changes about how forwarding works on each tier."""
    current_plan = str(user["plan"]) if user else "free"
    on, off = "✅", "❌"

    def row(label: str, values: dict[str, str]) -> str:
        return f"<b>{label}:</b>\n   🆓 {values['free']} | 🥈 {values['silver']} | 🥇 {values['gold']} | 💎 {values['platinum']}\n"

    lines = ["📊 <b>Compare All Plans</b>\n"]
    lines.append(row("Forward style", {
        "free": "Native (keeps 'Forwarded from' tag)", "silver": "Clean copy", "gold": "Clean copy", "platinum": "Clean copy",
    }))
    lines.append(row("Header / Footer", {"free": off, "silver": on, "gold": on, "platinum": on}))
    lines.append(row("Delay / Antiban speed control", {"free": off, "silver": on, "gold": on, "platinum": on}))
    lines.append(row("Blacklist / Whitelist filter", {"free": off, "silver": off, "gold": on, "platinum": on}))
    lines.append(row("Replace username/link/word", {"free": off, "silver": off, "gold": off, "platinum": on}))
    lines.append(row("Sender filter", {"free": off, "silver": off, "gold": off, "platinum": on}))
    lines.append(row("Watermark (on images only)", {"free": off, "silver": off, "gold": off, "platinum": on}))
    lines.append(row("Auto-delete timer", {"free": off, "silver": off, "gold": off, "platinum": on}))
    lines.append(row("Attach uploaded file", {"free": off, "silver": off, "gold": off, "platinum": on}))
    lines.append("")
    for key in ("free", "silver", "gold", "platinum"):
        plan = PLANS[key]
        daily = f"{plan.daily_messages}/day" if plan.daily_messages else "Unlimited"
        marker = " 👈 <i>your plan</i>" if key == current_plan else ""
        icon = {"free": "🆓", "silver": "🥈", "gold": "🥇", "platinum": "💎"}[key]
        lines.append(f"{icon} <b>{plan.name}</b>{marker} — {plan.tasks} tasks, {plan.sources_per_task} sources, {plan.destinations_per_task} destinations, {daily}")
    return "\n".join(lines)

def _format_name(user) -> str:
    return safe_html(user["first_name"] or user["username"] or user["telegram_user_id"])

async def _render_tasks(message: Message, db: Database, user_id: int) -> None:
    user = await db.get_user(user_id)
    language = language_for(user["preferred_language"]) if user else "en"
    tasks = await db.list_tasks(user_id)
    
    plan_name = str((user or {}).get("plan") or "free")
    plan = PLANS.get(plan_name, PLANS["free"])
    created = len(tasks)
    remaining = max(0, plan.tasks - created)
    summary_line = safe_t(language, "tasks_summary", plan=plan.name, created=created, remaining=remaining, total=plan.tasks)

    rows: list[list[InlineKeyboardButton]] = []
    if tasks:
        lines = [summary_line, ""]
        for task in tasks:
            status = "⏸️ Paused" if task["is_paused"] else "▶️ Active"
            safe_task_name = safe_html(task['task_name'])
            lines.append(f"• {safe_task_name} — {status}")

            action_btn = InlineKeyboardButton(
                text=f"▶️ Resume" if task["is_paused"] else f"⏸️ Pause",
                callback_data=f"task:{'resume' if task['is_paused'] else 'pause'}:{task['id']}"
            )
            rows.append([
                action_btn,
                InlineKeyboardButton(text="⚙️ Settings", callback_data=f"set:task:{task['id']}"),
                InlineKeyboardButton(text="🗑️ Delete", callback_data=f"task:delete:{task['id']}"),
            ])
        text = "\n".join(lines)
    else:
        text = f"{summary_line}\n\n{safe_t(language, 'no_tasks_short')}"
        
    rows.append([InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if message.from_user and message.from_user.is_bot:
        await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")

# ==========================================
# GLOBAL / CORE COMMANDS
# ==========================================

@router.message(Command("start"))
async def start(message: Message, command: CommandObject, db: Database, settings: Settings) -> None:
    user, is_new = await db.ensure_user_with_status(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if is_new and await db.mark_new_user_notified(message.from_user.id):
        await _notify_admins(
            message.bot, settings,
            f"🆕 New Dealskoti user\nName: {safe_html(message.from_user.full_name)}\nUsername: @{message.from_user.username or '—'}\nID: <code>{message.from_user.id}</code>"
        )
    args = (command.args if command else "") or ""
    if args.startswith("ref_"):
        with suppress(Exception):
            referrer_id = int(args[4:])
            if referrer_id != message.from_user.id:
                await db.create_referral(referrer_id, message.from_user.id)
    language = language_for(user["preferred_language"])
    if not user["language_selected"]:
        await message.answer("🌐 Choose your language / Apni language choose karo:", reply_markup=language_keyboard())
        return
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    await message.answer(await _menu_text(db, message.from_user.id, language), reply_markup=main_menu_keyboard(settings))

@router.message(Command("menu"))
async def menu_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        await message.answer(await _menu_text(db, message.from_user.id, language), reply_markup=main_menu_keyboard(settings))

# ==========================================
# HELP — DYNAMIC TWO-STEP CATEGORY SYSTEM
# ==========================================

# Feature -> minimum plan rank required (0=free, 1=silver, 2=gold, 3=platinum)
FEATURE_MIN_RANK: dict[str, int] = {
    "header": 0, "footer": 0,
    "blacklist": 2, "whitelist": 2,
    "replace_usernames": 3, "replace_links": 3, "replace_words": 3,
    "watermark": 3, "auto_delete_seconds": 3, "user_filter": 3,
}

HELP_CATEGORIES: dict[str, dict] = {
    "setting": {"command": "/setting", "min_rank": 0, "desc_key": "help_cat_setting", "features": []},
    "forwarding_controls": {
        "command": "/forwarding_controls", "min_rank": 0, "desc_key": "help_cat_forwarding",
        "features": ["watermark", "auto_delete_seconds", "user_filter"],
    },
    "filters_replacements": {
        "command": "/filters_replacements", "min_rank": 0, "desc_key": "help_cat_filters",
        "features": ["blacklist", "whitelist", "replace_usernames", "replace_links",
                     "replace_words", "header", "footer"],
    },
    "media_control": {
        "command": "/media_control", "min_rank": 3, "desc_key": "help_cat_media",
        "features": ["watermark", "auto_delete_seconds", "user_filter"],
    },
}

def _plan_rank(user) -> int:
    return PLAN_RANKS.get(str((user or {}).get("plan") or "free"), 0)

def _feature_label(feature: str) -> str:
    return FEATURE_DISPLAY.get(feature, feature.replace("_", " ").title())

def _help_intro_text(language: str, rank: int) -> str:
    lines = [f"{c['command']} – {safe_t(language, c['desc_key'])}"
             for c in HELP_CATEGORIES.values() if rank >= c["min_rank"]]
    return safe_t(language, "help_intro", commands="\n".join(lines))

@router.message(Command("help"))
async def help_command(message: Message, db: Database, settings: Settings) -> None:
    user = await _ensure_user(db, message)
    language = language_for(user["preferred_language"])
    text = _help_intro_text(language, _plan_rank(user))
    # Admin commands are only shown to authorized admins
    if _is_admin(settings, message.from_user.id):
        text += "\n\n" + safe_t(language, "admin_help_title", commands=admin_help())
    await message.answer(text, reply_markup=_nav_keyboard())

async def _render_help_category(message_obj, db: Database, user_id: int, key: str, *, edit: bool) -> None:
    user = await db.get_user(user_id)
    language = language_for(user["preferred_language"]) if user else "en"
    rank = _plan_rank(user)
    cat = HELP_CATEGORIES[key]
    title = f"{safe_html(cat['command'])} – {safe_t(language, cat['desc_key'])}"
    rows: list[list[InlineKeyboardButton]] = []
    for feature in cat["features"]:
        label = _feature_label(feature)
        if rank < FEATURE_MIN_RANK.get(feature, 3):
            rows.append([InlineKeyboardButton(text=f"🔒 {label}", callback_data=f"help:lock:{feature}")])
        else:
            rows.append([InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"help:feat:{key}:{feature}")])
    if not cat["features"]:
        rows.append([InlineKeyboardButton(text=safe_t(language, "open_settings_btn"), callback_data="menu:settings")])
    rows.append([InlineKeyboardButton(text=safe_t(language, "back_to_help"), callback_data="help:home")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    text = f"{title}\n\n{safe_t(language, 'help_configure_hint')}"
    if edit and hasattr(message_obj, "edit_text"):
        await message_obj.edit_text(text, reply_markup=markup)
    else:
        await message_obj.answer(text, reply_markup=markup)

@router.message(Command("forwarding_controls"))
async def help_forwarding_controls_cmd(message: Message, db: Database) -> None:
    await _render_help_category(message, db, message.from_user.id, "forwarding_controls", edit=False)

@router.message(Command("filters_replacements"))
async def help_filters_replacements_cmd(message: Message, db: Database) -> None:
    await _render_help_category(message, db, message.from_user.id, "filters_replacements", edit=False)

@router.message(Command("media_control"))
async def help_media_control_cmd(message: Message, db: Database) -> None:
    await _render_help_category(message, db, message.from_user.id, "media_control", edit=False)

@router.callback_query(F.data == "help:home")
async def help_home_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    language = language_for(user["preferred_language"])
    await callback.message.edit_text(_help_intro_text(language, _plan_rank(user)), reply_markup=_nav_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("help:lock:"))
async def help_lock_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    feature = callback.data.split(":", 2)[2]
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    language = language_for(user["preferred_language"])
    required_plan = {0: "Free", 1: "Silver", 2: "Gold", 3: "Platinum"}.get(FEATURE_MIN_RANK.get(feature, 3), "Platinum")
    await callback.message.edit_text(
        safe_t(language, "feature_locked", feature=_feature_label(feature), required_plan=required_plan),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=safe_t(language, "view_plans_btn"), callback_data="menu:plans")],
            [InlineKeyboardButton(text=safe_t(language, "back_to_help"), callback_data="help:home")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("help:feat:"))
async def help_feat_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    _, _, _, feature = callback.data.split(":")
    language = await _language_for_callback(db, callback)
    await callback.message.edit_text(
        f"✏️ <b>{safe_html(_feature_label(feature))}</b>\n\n{safe_t(language, 'help_configure_hint')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=safe_t(language, "open_settings_btn"), callback_data="menu:settings")],
            [InlineKeyboardButton(text=safe_t(language, "back_to_help"), callback_data="help:home")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()

@router.message(Command("adminhelp"))
async def admin_help_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if not _is_admin(settings, message.from_user.id):
        await message.answer(safe_t(language, "admin_only"))
        return
    await message.answer(safe_t(language, "admin_help_title", commands=admin_help()))

@router.message(Command("support", "contact"))
async def support_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    intro = safe_t(language, "support_intro")
    await message.answer(
        f"{intro}\n\n🔗 {settings.support_bot_link or 'support'}",
        reply_markup=_nav_keyboard(),
    )

@router.message(Command("updates", "channel"))
async def updates_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    intro = safe_t(language, "updates_intro")
    await message.answer(
        intro,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Join Updates Channel", url=f"https://t.me/{settings.update_channel_username.lstrip('@')}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ])
    )

# ==========================================
# LANGUAGE FLOW
# ==========================================

@router.message(Command("language"))
async def language_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(safe_t(language, "choose_language"), reply_markup=language_keyboard())

@router.callback_query(F.data.startswith("language:"))
async def choose_language(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    choice = callback.data.split(":", 1)[1]
    if choice == "choose":
        await callback.message.edit_text("🌐 Choose your language:", reply_markup=language_keyboard())
        await callback.answer()
        return
    if choice not in {"en", "hinglish"}:
        await callback.answer("Invalid language", show_alert=True)
        return
    await db.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    await db.set_language(callback.from_user.id, choice)
    language = language_for(choice)
    await callback.message.edit_text(safe_t(language, "language_saved"))
    if await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.message.answer(await _menu_text(db, callback.from_user.id, language), reply_markup=main_menu_keyboard(settings))
    await callback.answer()

# ==========================================
# FAQ FLOW (ACCORDION) - FIXED PAGINATION
# ==========================================

@router.message(Command("faq"))
async def faq_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    faqs = FAQS[language_for(language)]
    total_pages = (len(faqs) + 4) // 5
    await message.answer(_faq_view_text(language, 1, -1), reply_markup=faq_accordion_keyboard(language, 1, -1), parse_mode="HTML")

@router.callback_query(F.data.startswith("faq:page:"))
async def faq_page(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    page = int(callback.data.split(":")[2])
    await _safe_edit(callback.message, _faq_view_text(language, page, -1), faq_accordion_keyboard(language, page, -1))
    await callback.answer()

@router.callback_query(F.data.startswith("faq:expand:"))
async def faq_expand(callback: CallbackQuery, db: Database) -> None:
    """Backwards-compatible handler for old faq:expand:<page>:<index> callbacks."""
    if callback.message is None:
        await callback.answer()
        return
    language = await _language_for_callback(db, callback)
    parts = callback.data.split(":")
    # old format: faq:expand:page:index
    if len(parts) >= 4:
        page, index = int(parts[2]), int(parts[3])
    else:
        page, index = 1, int(parts[2])
    await _safe_edit(callback.message, _faq_view_text(language, page, index), faq_accordion_keyboard(language, page, index))
    await callback.answer()

@router.callback_query(F.data.startswith("faq:item:"))
async def faq_item(callback: CallbackQuery, db: Database) -> None:
    """Opens an FAQ: edits the whole message to show Q & A."""
    if callback.message is None:
        await callback.answer()
        return
    language = await _language_for_callback(db, callback)
    parts = callback.data.split(":")
    if len(parts) < 4:
        return await callback.answer()
    page, index = int(parts[2]), int(parts[3])
    faqs = FAQS[language_for(language)]
    if index < 0 or index >= len(faqs):
        return await callback.answer()
    await _safe_edit(callback.message, _faq_view_text(language, page, index), faq_accordion_keyboard(language, page, index))
    await callback.answer()

@router.callback_query(F.data.startswith("faq:collapse:"))
async def faq_collapse(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        await callback.answer()
        return
    language = await _language_for_callback(db, callback)
    page = int(callback.data.split(":")[2])
    await _safe_edit(callback.message, _faq_view_text(language, page, -1), faq_accordion_keyboard(language, page, -1))
    await callback.answer()

# ==========================================
# MAIN MENU NAVIGATION
# ==========================================

@router.callback_query(F.data == "gate:check")
async def gate_check(callback: CallbackQuery, db: Database, settings: Settings, forwarding: ForwardingEngine) -> None:
    """Handles the "I've Joined" button on the force-join prompt."""
    language = await _language_for_callback(db, callback)
    user_id = callback.from_user.id
    is_member = await user_is_member(callback.bot, settings, user_id)
    await db.set_membership(user_id, is_member)

    if not is_member:
        await callback.answer(
            "You have not joined yet. Please join the channel first, then tap again."
            if language == "en" else
            "Aap ne abhi join nahi kiya. Pehle channel join karein, phir dobara dabayein.",
            show_alert=True,
        )
        return

    # Membership restored — bring paused tasks back and reload the engine.
    with suppress(Exception):
        await db.resume_channel_gate_tasks(user_id)
    with suppress(Exception):
        await forwarding.refresh_user(user_id)

    await callback.answer("Verified ✅", show_alert=False)
    if callback.message is not None:
        await _safe_edit(
            callback.message,
            await _menu_text(db, user_id, language),
            main_menu_keyboard(settings),
        )

@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    if await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await _safe_edit(callback.message, await _menu_text(db, callback.from_user.id, language), main_menu_keyboard(settings))
    await callback.answer()

@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    language = await _language_for_callback(db, callback)
    if callback.message:
        await _safe_edit(callback.message, await _menu_text(db, callback.from_user.id, language), main_menu_keyboard(settings))
    await callback.answer("Cancelled", show_alert=False)

# ==========================================
# ACCOUNT & LOGIN
# ==========================================

async def _account_text(db: Database, user_id: int, user, language: str) -> str:
    session = "connected" if await db.has_active_session(user_id) else "not connected"
    ist_zone = ZoneInfo("Asia/Kolkata")
    expiry = user["plan_expiry"].astimezone(ist_zone).strftime("%d %b %Y, %I:%M %p IST") if user["plan_expiry"] else "Lifetime"
    last_payment = await db.get_last_captured_payment(user_id)
    plan_started = last_payment["created_at"].astimezone(ist_zone).strftime("%d %b %Y") if last_payment and last_payment["created_at"] else "—"
    txn_id = last_payment["payment_id"] if last_payment and last_payment["payment_id"] else "—"
    tasks = await db.count_tasks(user_id)
    usage = await db.daily_usage(user_id)
    return safe_t(
        language, "account_details",
        name=_format_name(user),
        username=f"@{user['username']}" if user["username"] else "—",
        user_id=user["telegram_user_id"],
        plan=str(user["plan"]).title(),
        plan_started=plan_started,
        expiry=expiry,
        txn_id=txn_id,
        payment="Active" if user["plan"] != "free" else "No paid payment",
        session=session,
        tasks=tasks,
        forwarding=f"{usage} messages today",
        membership="Verified" if user["updates_channel_member"] else "Not verified",
        user_language=user["preferred_language"],
    )

@router.message(Command("account", "myaccount"))
async def account_command(message: Message, db: Database) -> None:
    """Primary /account command. /myaccount is the backward-compatible alias
    but only /account shows in /help and the Telegram command menu."""
    user = await _ensure_user(db, message)
    language = language_for(user["preferred_language"])
    await message.answer(await _account_text(db, message.from_user.id, user, language), reply_markup=_nav_keyboard())

@router.callback_query(F.data == "menu:account")
async def menu_account(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    language = language_for(user["preferred_language"])
    await callback.message.edit_text(await _account_text(db, callback.from_user.id, user, language), reply_markup=_nav_keyboard())
    await callback.answer()

@router.callback_query(F.data == "menu:refer")
async def menu_refer(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    me = await callback.bot.me()
    link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    count = await db.count_referrals(callback.from_user.id)
    await callback.message.edit_text(
        safe_t(language, "refer_intro", link=link, count=count),
        reply_markup=_nav_keyboard(),
    )
    await callback.answer()

@router.message(Command("disconnect"))
async def disconnect_command(message: Message, db: Database, telethon: TelethonService, forwarding: ForwardingEngine, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    await message.answer(
        safe_t(language, "disconnect_confirm"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Disconnect", callback_data="auth:disconnect")],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="menu:home")]
        ])
    )

@router.callback_query(F.data == "auth:disconnect")
async def auth_disconnect_callback(callback: CallbackQuery, db: Database, telethon: TelethonService, forwarding: ForwardingEngine, settings: Settings) -> None:
    language = await _language_for_callback(db, callback)
    await forwarding.remove_user(callback.from_user.id)
    await telethon.disconnect(callback.from_user.id)
    await _notify_admins(callback.bot, settings, f"🔌 Session disconnected\nUser ID: <code>{callback.from_user.id}</code>")
    if callback.message:
        await callback.message.edit_text(
            safe_t(language, "disconnect_done"),
            reply_markup=_nav_keyboard()
        )
    await callback.answer()

@router.message(Command("connect", "login"))
async def connect_command(message: Message, state: FSMContext, db: Database, settings: Settings, telethon: TelethonService) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language): return
    if await db.has_active_session(message.from_user.id):
        await message.answer(
            safe_t(language, "already_connected"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=safe_t(language, "reconnect_anyway"), callback_data="connect:force")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ])
        )
        return
    await telethon.cancel_login(message.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await message.answer(safe_t(language, "login_phone"), reply_markup=_nav_keyboard(include_cancel=True))

@router.callback_query(F.data == "menu:connect")
async def menu_connect(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext, telethon: TelethonService) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language): return
    if await db.has_active_session(callback.from_user.id):
        await callback.message.edit_text(
            safe_t(language, "already_connected"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=safe_t(language, "reconnect_anyway"), callback_data="connect:force")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ])
        )
        await callback.answer()
        return
    await telethon.cancel_login(callback.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await callback.message.edit_text(safe_t(language, "login_phone"), reply_markup=_nav_keyboard(include_cancel=True))
    await callback.answer()

@router.callback_query(F.data == "connect:force")
async def connect_force(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    await telethon.cancel_login(callback.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await callback.message.edit_text(safe_t(language, "login_phone"), reply_markup=_nav_keyboard(include_cancel=True))
    await callback.answer()

@router.message(LoginStates.waiting_phone)
async def login_phone(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    phone = message.text.strip().replace(" ", "").replace("-", "")
    if phone == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())
        return
    if not re.fullmatch(r"\+\d{8,15}", phone):
        await message.answer(safe_t(language, "invalid_phone"), reply_markup=_nav_keyboard(include_cancel=True))
        return
    try:
        await telethon.start_phone_login(message.from_user.id, phone)
    except Exception as exc:
        await state.clear()
        await message.answer(safe_t(language, "login_failed"), reply_markup=_nav_keyboard())
        return
    await state.set_state(LoginStates.waiting_pin)
    await message.answer(safe_t(language, "login_pin"), reply_markup=_nav_keyboard(include_cancel=True))

@router.message(LoginStates.waiting_pin)
async def login_pin(message: Message, state: FSMContext, telethon: TelethonService, db: Database, forwarding: ForwardingEngine, settings: Settings) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())
        return
    try:
        result = await telethon.submit_pin(message.from_user.id, message.text.strip())
    except ValueError as exc:
        await message.answer(f"⚠️ {safe_html(exc)}")
        return
    if result == "2fa_required":
        await state.set_state(LoginStates.waiting_2fa)
        await message.answer(safe_t(language, "login_2fa"), reply_markup=_nav_keyboard(include_cancel=True))
        return
    await state.clear()
    await forwarding.refresh_user(message.from_user.id)
    await _notify_admins(message.bot, settings, f"🔌 Connected\nID: <code>{message.from_user.id}</code>")
    await message.answer(safe_t(language, "login_success"), reply_markup=_nav_keyboard())

@router.message(LoginStates.waiting_2fa)
async def login_2fa(message: Message, state: FSMContext, telethon: TelethonService, db: Database, forwarding: ForwardingEngine, settings: Settings) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())
        return
    try:
        await telethon.submit_2fa(message.from_user.id, message.text)
    except ValueError as exc:
        await message.answer(f"⚠️ {safe_html(exc)}")
        return
    await state.clear()
    await forwarding.refresh_user(message.from_user.id)
    await _notify_admins(message.bot, settings, f"🔌 Connected\nID: <code>{message.from_user.id}</code>")
    await message.answer(safe_t(language, "login_success"), reply_markup=_nav_keyboard())

# ==========================================
# PLANS & SUBSCRIPTION
# ==========================================

def _render_plans_prefix(user: dict, language: str) -> str:
    if user and user["plan"] != "free":
        ist_zone = ZoneInfo("Asia/Kolkata")
        current_plan = str(user["plan"]).title()
        expiry_ist = user["plan_expiry"].astimezone(ist_zone).strftime("%d %b %Y, %I:%M %p IST") if user["plan_expiry"] else "Lifetime"
        return f"👤 <b>Current Plan:</b> {current_plan}\n⏳ <b>Expiry:</b> {expiry_ist}\n\n" if language == "en" else f"👤 <b>Aapka Plan:</b> {current_plan}\n⏳ <b>Expiry:</b> {expiry_ist}\n\n"
    return "👤 <b>Current Plan:</b> Free\n\n" if language == "en" else "👤 <b>Aapka Plan:</b> Free\n\n"

@router.message(Command("plans", "subscribe"))
async def plans_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    user = await db.get_user(message.from_user.id)
    await message.answer(_render_plans_prefix(user, language) + safe_t(language, "choose_plan"), reply_markup=plans_keyboard())

@router.callback_query(F.data == "menu:plans")
async def menu_plans(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    user = await db.get_user(callback.from_user.id)
    await callback.message.edit_text(_render_plans_prefix(user, language) + safe_t(language, "choose_plan"), reply_markup=plans_keyboard())
    await callback.answer()

@router.callback_query(F.data == "plans:compare")
async def plans_compare_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    user = await db.get_user(callback.from_user.id)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Back to Plans", callback_data="menu:plans")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])
    await callback.message.edit_text(_plans_comparison_text(user, language), reply_markup=markup, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("plan:"))
async def plan_details(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    plan_name = callback.data.split(":", 1)[1]
    if plan_name not in PLANS: return await callback.answer("Invalid plan", show_alert=True)
    language = await _language_for_callback(db, callback)
    plan = PLANS[plan_name]
    usdt_price = usdt_amount_usd(plan_name, "monthly")
    usdt_line = f"\n🪙 USDT Price: ${usdt_price} / month" if usdt_price > 0 else ""
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans")],[InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]]) if plan_name == "free" else cycles_keyboard(plan_name)
    await callback.message.edit_text(safe_t(language, "plan_details", plan=plan.name, features=_plan_features(plan_name), monthly=plan.monthly_rupees) + usdt_line, reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data.startswith("cycle:"))
async def billing_cycle(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    _, plan_name, cycle = callback.data.split(":")
    if plan_name not in PLANS or cycle not in {"weekly", "monthly", "yearly"}: return await callback.answer("Invalid option", show_alert=True)
    language = await _language_for_callback(db, callback)
    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(plan_name, cycle, first_paid_order=first_order)
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm Payment", callback_data=f"payment:confirm:{plan_name}:{cycle}")],
        [InlineKeyboardButton(text="🪙 Pay with USDT", callback_data=f"payusdt:{plan_name}:{cycle}")],
        [InlineKeyboardButton(text="◀️ Back", callback_data=f"plan:{plan_name}")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])
    await callback.message.edit_text(safe_t(language, "billing_details", plan=PLANS[plan_name].name, cycle=cycle.title(), original=format_paise(original), discount=format_paise(discount), payable=format_paise(payable)), reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data.startswith("payment:confirm:"))
async def confirm_payment(callback: CallbackQuery, db: Database, billing: RazorpayBilling, settings: Settings) -> None:
    if callback.message is None: return
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[2], parts[3]
    language = await _language_for_callback(db, callback)
    if plan_name not in PLANS or plan_name == "free" or cycle not in {"weekly", "monthly", "yearly"}:
        return await callback.answer("Invalid option", show_alert=True)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language): return
    connect_msg = await _require_connected(db, callback.from_user.id, language)
    if connect_msg:
        await callback.message.edit_text(connect_msg, reply_markup=_connect_required_keyboard())
        return await callback.answer()
    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(plan_name, cycle, first_paid_order=first_order)
    if payable <= 0:
        return await callback.answer("Invalid option", show_alert=True)
    # Razorpay's reference_id has a strict 40-character limit. Using the full
    # plan/cycle words + a 10-char suffix could exceed that (e.g. "platinum"
    # + "monthly" pushed it to 41 chars and Razorpay rejected the link).
    # Single-letter codes + a shorter suffix keep this comfortably under 40.
    plan_code = plan_name[0]
    cycle_code = cycle[0]
    unique_suffix = uuid4().hex[:12]
    receipt = f"dk_{callback.from_user.id}_{plan_code}{cycle_code}_{unique_suffix}"
    try:
        link = await billing.create_payment_link(amount_paise=payable, receipt=receipt, plan=plan_name, cycle=cycle, user_id=callback.from_user.id)
        await db.save_payment(callback.from_user.id, link.link_id, plan_name, cycle, original, discount, payable)
    except BillingError as exc:
        logger.warning("Payment link creation failed for %s: %s", callback.from_user.id, exc)
        return await callback.answer(f"{safe_t(language, 'payment_failed')}\n\n{exc}"[:200], show_alert=True)

    await _safe_edit(
        callback.message,
        safe_t(language, "payment_link", plan=PLANS[plan_name].name, cycle=cycle.title(), amount=format_paise(payable)),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay Now", url=link.short_url)],
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ])
    )
    await callback.answer()

# ==========================================
# USDT MANUAL PAYMENTS
# ==========================================

@router.callback_query(F.data.startswith("payusdt:"))
async def payusdt_start(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None: return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[1], parts[2]
    if plan_name not in PLANS or plan_name == "free" or cycle not in {"weekly", "monthly", "yearly"}:
        return await callback.answer("Invalid option", show_alert=True)
    language = await _language_for_callback(db, callback)
    if not settings.usdt_wallet_address:
        return await callback.answer(safe_t(language, "usdt_unavailable"), show_alert=True)
    connect_msg = await _require_connected(db, callback.from_user.id, language)
    if connect_msg:
        await callback.message.edit_text(connect_msg, reply_markup=_connect_required_keyboard())
        return await callback.answer()
    amount = usdt_amount_usd(plan_name, cycle)
    await _safe_edit(
        callback.message,
        safe_t(language, "usdt_instructions",
               plan=PLANS[plan_name].name, cycle=cycle.title(), amount=amount,
               network=safe_html(settings.usdt_network), wallet=safe_html(settings.usdt_wallet_address)),
        InlineKeyboardMarkup(inline_keyboard=[
            # Distinct prefix so this never collides with the "payusdt:" filter above.
            [InlineKeyboardButton(text=safe_t(language, "usdt_paid_btn"), callback_data=f"usdttxid:{plan_name}:{cycle}")],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"plan:{plan_name}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()

@router.callback_query(F.data.startswith("usdttxid:"))
async def payusdt_txid_prompt(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.message is None: return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[1], parts[2]
    if plan_name not in PLANS or cycle not in {"weekly", "monthly", "yearly"}:
        return await callback.answer("Invalid option", show_alert=True)
    language = await _language_for_callback(db, callback)
    await state.set_state(UsdtStates.waiting_txid)
    await state.update_data(usdt_plan=plan_name, usdt_cycle=cycle)
    await _safe_edit(
        callback.message,
        safe_t(language, "usdt_txid_prompt"),
        _nav_keyboard(back="menu:plans", include_cancel=True),
    )
    await callback.answer()

@router.message(UsdtStates.waiting_txid)
async def payusdt_txid_submit(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    txid = message.text.strip()
    if txid == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    if len(txid) < 10:
        return await message.answer(safe_t(language, "usdt_txid_prompt"))
    data = await state.get_data()
    plan_name = str(data.get("usdt_plan") or "silver")
    cycle = str(data.get("usdt_cycle") or "monthly")
    amount = usdt_amount_usd(plan_name, cycle)
    request_id = await db.create_usdt_request(message.from_user.id, plan_name, cycle, amount, txid)
    await state.clear()
    await message.answer(safe_t(language, "usdt_submitted"), reply_markup=_nav_keyboard())
    admin_text = (
        f"🪙 <b>USDT Payment Request</b>\n\n"
        f"Request ID: <code>{request_id}</code>\n"
        f"User: {safe_html(message.from_user.username or str(message.from_user.id))} (<code>{message.from_user.id}</code>)\n"
        f"Plan: <b>{PLANS[plan_name].name}</b> ({cycle.title()})\n"
        f"Amount: <b>{amount} USDT</b>\n"
        f"TXID:\n<code>{safe_html(txid)}</code>"
    )
    for admin_id in settings.admin_telegram_ids:
        try:
            await message.bot.send_message(
                admin_id,
                admin_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Approve", callback_data=f"usdt:approve:{request_id}"),
                     InlineKeyboardButton(text="❌ Reject", callback_data=f"usdt:reject:{request_id}")],
                ]),
            )
        except Exception:
            pass

@router.callback_query(F.data.startswith("usdt:approve:") | F.data.startswith("usdt:reject:"))
async def usdt_review(callback: CallbackQuery, db: Database, settings: Settings, forwarding: ForwardingEngine) -> None:
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)
    if callback.message is None: return
    parts = callback.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return await callback.answer("Invalid option", show_alert=True)
    action, request_id = parts[1], int(parts[2])
    req = await db.get_usdt_request(request_id)
    if not req: return await callback.answer("Not found", show_alert=True)
    if str(req["status"]) != "pending":
        return await callback.answer(f"Already {req['status']}", show_alert=True)

    target_user_id = int(req["user_id"])
    target_user = await db.get_user(target_user_id)
    language = language_for(target_user["preferred_language"]) if target_user else "en"
    plan_key = str(req["plan"])
    plan_label = PLANS[plan_key].name if plan_key in PLANS else plan_key.title()

    if action == "approve":
        days = duration_days(str(req["cycle"]))
        ok = await db.set_plan(target_user_id, plan_key, days)
        if not ok: return await callback.answer("Activation failed", show_alert=True)
        await db.set_usdt_status(request_id, "approved", callback.from_user.id)
        with suppress(Exception):
            await forwarding.refresh_user(target_user_id)
        user = await db.get_user(target_user_id)
        expiry = user["plan_expiry"] if user else None
        expiry_str = expiry.astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST") if expiry else "—"
        with suppress(Exception):
            await callback.bot.send_message(
                target_user_id,
                safe_t(language, "usdt_approved_user", plan=plan_label, days=days, expiry=expiry_str),
                parse_mode="HTML",
            )
        with suppress(TelegramBadRequest):
            await callback.message.edit_text((callback.message.html_text or "") + "\n\n✅ APPROVED", reply_markup=None)
        return await callback.answer("Approved & activated")
    else:
        await db.set_usdt_status(request_id, "rejected", callback.from_user.id)
        with suppress(Exception):
            await callback.bot.send_message(
                target_user_id,
                safe_t(language, "usdt_rejected_user", txid=safe_html(str(req["txid"]))),
                parse_mode="HTML",
            )
        with suppress(TelegramBadRequest):
            await callback.message.edit_text((callback.message.html_text or "") + "\n\n❌ REJECTED", reply_markup=None)
        return await callback.answer("Rejected")

# ==========================================
# PLATINUM FILE UPLOAD (/upload_file)
# ==========================================

def _is_platinum_file_ready(plan_name: str) -> bool:
    return plan_name == "platinum"

async def _start_upload(message_obj, db: Database, settings: Settings, user_id: int, language: str) -> None:
    user = await db.get_user(user_id)
    plan_name = str(user["plan"]) if user else "free"
    if not _is_platinum_file_ready(plan_name):
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
    else:
        await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")

@router.message(Command("upload_file"))
async def upload_file_cmd(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    connect_msg = await _require_connected(db, message.from_user.id, language)
    if connect_msg:
        return await message.answer(connect_msg, reply_markup=_connect_required_keyboard())
    user = await db.get_user(message.from_user.id)
    if _is_platinum_file_ready(str(user["plan"]) if user else "free") and settings.file_storage_channel_id:
        await state.set_state(UploadStates.waiting_file)
    await _start_upload(message, db, settings, message.from_user.id, language)

@router.callback_query(F.data == "menu:upload")
async def menu_upload(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    connect_msg = await _require_connected(db, callback.from_user.id, language)
    if connect_msg:
        await callback.message.edit_text(connect_msg, reply_markup=_connect_required_keyboard())
        return await callback.answer()
    user = await db.get_user(callback.from_user.id)
    if _is_platinum_file_ready(str(user["plan"]) if user else "free") and settings.file_storage_channel_id:
        await state.set_state(UploadStates.waiting_file)
    await _start_upload(callback.message, db, settings, callback.from_user.id, language)
    await callback.answer()

async def _do_store_upload(bot: Bot, db: Database, telethon: TelethonService, settings: Settings,
                            user_id: int, file_name: str, ext: str, size: int, file_id: str | None,
                            src_chat_id: int, src_message_id: int, language: str, reply_chat_id: int) -> None:
    """Downloads + stores the file, then reports success/failure to reply_chat_id.
    Shared by the direct-upload path (no existing file) and the replace-confirm path."""
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)
    local_path = uploads_dir / f"{user_id}_{uuid4().hex[:8]}_{file_name}"

    BOT_API_LIMIT = 20 * 1024 * 1024
    try:
        if size <= BOT_API_LIMIT and file_id:
            tg_file = await bot.get_file(file_id)
            await bot.download_file(tg_file.file_path, destination=str(local_path))
        else:
            ok = await telethon.download_media_big(
                settings.telegram_bot_token, src_chat_id, src_message_id, str(local_path)
            )
            if not ok:
                raise RuntimeError("MTProto download returned no media")
    except Exception as exc:
        logger.error(f"upload download failed: {exc}")
        await _notify_admins(bot, settings, f"⚠️ Upload download failed for {user_id}: {exc}")
        await bot.send_message(
            reply_chat_id,
            "⚠️ Download failed. Please try again.\n"
            "If it keeps failing, make sure the file is sent as a Document (File) and not compressed.",
        )
        return

    channel_msg_id = None
    try:
        sent = await bot.send_document(
            settings.file_storage_channel_id,
            document=FSInputFile(str(local_path)),
            caption=f"storage:{user_id}",
        )
        channel_msg_id = sent.message_id
    except Exception as exc:
        logger.warning(f"storage-channel copy failed (file kept locally): {exc}")
        await _notify_admins(bot, settings,
                             f"⚠️ Storage channel copy failed for user {user_id}: {exc}\n"
                             f"Check that the bot is an ADMIN in the storage channel ({settings.file_storage_channel_id}).")

    # save_stored_file() replaces any existing row for this user AND deletes the old
    # physical file from disk — a user can only ever have one stored file at a time.
    await db.save_stored_file(user_id, file_name, ext, size, str(local_path), channel_msg_id, file_id)
    size_str = f"{size / (1024*1024):.1f} MB" if size >= 1024*1024 else f"{max(1, size // 1024)} KB"
    await bot.send_message(
        reply_chat_id,
        safe_t(language, "upload_success", name=safe_html(file_name), size=size_str),
        reply_markup=_nav_keyboard(),
        parse_mode="HTML",
    )

@router.message(UploadStates.waiting_file)
async def upload_receive(message: Message, state: FSMContext, db: Database, telethon: TelethonService, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if message.text and message.text.strip() == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    doc = message.document or (message.photo[-1] if message.photo else None) or message.video or message.audio
    if doc is None:
        return await message.answer(safe_t(language, "upload_prompt", max=settings.max_file_size_mb), parse_mode="HTML")

    file_name = getattr(doc, "file_name", None) or f"file_{uuid4().hex[:8]}.jpg"
    ext = (os.path.splitext(file_name)[1].lower().lstrip(".") or "bin")[:32]
    size = int(getattr(doc, "file_size", 0) or 0)
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if size > max_bytes:
        return await message.answer(safe_t(language, "upload_too_big", max=settings.max_file_size_mb), parse_mode="HTML")

    # A user can only have ONE stored file. If they already have one, confirm
    # before replacing it instead of silently overwriting.
    existing = await db.get_stored_file(message.from_user.id)
    if existing is not None:
        await state.update_data(pending_upload={
            "file_name": file_name, "ext": ext, "size": size,
            "file_id": getattr(doc, "file_id", None),
            "chat_id": message.chat.id, "message_id": message.message_id,
        })
        return await message.answer(
            safe_t(language, "upload_replace_confirm", old_name=safe_html(existing["file_name"]), new_name=safe_html(file_name)),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Yes, Replace", callback_data="upload:replace:yes")],
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="upload:replace:no")],
            ]),
            parse_mode="HTML",
        )

    await state.clear()
    await _do_store_upload(
        message.bot, db, telethon, settings, message.from_user.id,
        file_name, ext, size, getattr(doc, "file_id", None),
        message.chat.id, message.message_id, language, message.chat.id,
    )

@router.callback_query(F.data.startswith("upload:replace:"))
async def upload_replace_cb(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService, settings: Settings) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    decision = callback.data.rsplit(":", 1)[1]
    data = await state.get_data()
    pending = data.get("pending_upload")
    await state.clear()
    if decision != "yes" or not pending:
        await callback.message.edit_text("↩️ Cancelled.", reply_markup=_nav_keyboard())
        return await callback.answer()
    with suppress(TelegramBadRequest):
        await callback.message.edit_text("⏳ Uploading…", reply_markup=None)
    await _do_store_upload(
        callback.bot, db, telethon, settings, callback.from_user.id,
        pending["file_name"], pending["ext"], pending["size"], pending.get("file_id"),
        pending["chat_id"], pending["message_id"], language, callback.message.chat.id,
    )
    await callback.answer()

# ==========================================
# TASKS
# ==========================================

@router.message(Command("tasks", "viewtasks"))
async def view_tasks(message: Message, db: Database) -> None:
    await _render_tasks(message, db, message.from_user.id)

@router.callback_query(F.data == "menu:tasks")
async def menu_tasks(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language): return
    await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer()

@router.message(Command("newtask", "createtask"))
async def new_task_cmd(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language): return
    connect_msg = await _require_connected(db, message.from_user.id, language)
    if connect_msg:
        return await message.answer(connect_msg, reply_markup=_connect_required_keyboard())
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    if await db.count_tasks(message.from_user.id) >= plan.tasks:
        await message.answer(
            f"⚠️ Task limit reached for {plan.name} plan." if language == "en" else f"⚠️ {plan.name} plan ki task limit khatam ho gayi.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Upgrade", callback_data="menu:plans")]])
        )
        return
    await state.set_state(TaskStates.waiting_name)
    await message.answer(safe_t(language, "task_name"), reply_markup=_nav_keyboard(include_cancel=True))

@router.callback_query(F.data == "task:create")
async def task_create_cb(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language): return
    connect_msg = await _require_connected(db, callback.from_user.id, language)
    if connect_msg:
        await callback.message.edit_text(connect_msg, reply_markup=_connect_required_keyboard())
        return await callback.answer()
    user = await db.get_user(callback.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    if await db.count_tasks(callback.from_user.id) >= plan.tasks:
        await callback.message.edit_text(f"⚠️ Task limit reached for {plan.name}.", reply_markup=_nav_keyboard())
        return await callback.answer()
    await state.set_state(TaskStates.waiting_name)
    await callback.message.edit_text(safe_t(language, "task_name"), reply_markup=_nav_keyboard(include_cancel=True))
    await callback.answer()

@router.message(TaskStates.waiting_name)
async def task_name(message: Message, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    await state.update_data(task_name=message.text.strip()[:120])
    await state.set_state(TaskStates.waiting_source)
    await _render_chat_picker(message, db, telethon, state, message.from_user.id, "src", language)

def _text_or_forwarded_chat_id(message: Message) -> str | None:
    if message.text: return message.text.strip()
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin is not None else None
    if chat is not None and getattr(chat, "id", None) is not None: return str(chat.id)
    legacy_chat = getattr(message, "forward_from_chat", None)
    if legacy_chat is not None and getattr(legacy_chat, "id", None) is not None: return str(legacy_chat.id)
    return None

# ==========================================
# CHANNEL PICKER (RECENT CHATS + NUMBER KEYPAD)
# ==========================================

CHANNEL_INPUT_RE = re.compile(r"^(?:@[\w]{2,64}|https?://t\.me/\S+|t\.me/\S+|-?\d{5,})$", re.IGNORECASE)

PICKER_DIALOG_LIMIT = 30
PICKER_BUTTONS_PER_ROW = 5

def _picker_field_state(data: dict, field: str) -> tuple[list, list]:
    """Returns (selected_entities, all_dialogs) for 'src' or 'dst'."""
    selected = list(data.get("sources" if field == "src" else "destinations") or [])
    dialogs = list(data.get("picker_dialogs") or [])
    return selected, dialogs

def _picker_keyboard(dialogs: list, selected_ids: set[int], field: str, language: str) -> InlineKeyboardMarkup:
    """A numeric keypad — one button per listed chat — so nothing has to be typed.
    Selected entries are shown with a tick, tapping again deselects."""
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

async def _render_chat_picker(message_obj, db: Database, telethon: TelethonService, state: FSMContext,
                              user_id: int, field: str, language: str, *, refresh: bool = False,
                              edit: bool = False) -> None:
    """Renders the chat selector: a numbered text list plus a tap-to-select number keypad.
    Typing a number or forwarding a message still works as a fallback."""
    data = await state.get_data()
    selected, dialogs = _picker_field_state(data, field)
    if refresh or not dialogs:
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
        mark = " ✅" if did in selected_ids else ""
        lines.append(f"{idx + 1}. {label}{mark}")

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

async def _picker_toggle_number(message_obj, state: FSMContext, db: Database, telethon: TelethonService,
                                user_id: int, field: str, language: str, number: int, *,
                                edit: bool = False) -> bool:
    """Toggles dialog #number (1-based) for 'src'/'dst'. Re-renders the list. Returns True if handled."""
    data = await state.get_data()
    key = "sources" if field == "src" else "destinations"
    selected, dialogs = _picker_field_state(data, field)
    idx = number - 1
    if idx < 0 or idx >= len(dialogs):
        return False
    entity = dialogs[idx]
    eid = int(entity.get("id", 0))
    sel_ids = {int(e.get("id", 0)) for e in selected}
    if eid in sel_ids:
        selected = [e for e in selected if int(e.get("id", 0)) != eid]
    else:
        user = await db.get_user(user_id)
        plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
        limit = plan.sources_per_task if field == "src" else plan.destinations_per_task
        if len(selected) >= limit:
            await message_obj.answer(safe_t(language, "picker_limit_reached", limit=limit), parse_mode="HTML")
            return True
        selected.append(entity)
    await state.update_data({key: selected})
    await _render_chat_picker(message_obj, db, telethon, state, user_id, field, language, edit=edit)
    return True

async def _reply_or_edit(message_obj, text: str, keyboard, edit: bool) -> None:
    """Edits the picker message in place when we came from a button tap, otherwise
    posts a new message. Keeps a single tidy message instead of a growing chat."""
    if edit and hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        try:
            await _safe_edit(message_obj, text, keyboard)
            return
        except TelegramBadRequest:
            pass
    await message_obj.answer(text, parse_mode="HTML", reply_markup=keyboard)

async def _finish_sources(message_obj, state: FSMContext, db: Database, telethon: TelethonService,
                          forwarding: ForwardingEngine, user_id: int, language: str, *,
                          edit: bool = False) -> str | None:
    """Confirms the selected sources. Shared by the /done command and the Done button.
    Returns a short toast string when the caller is a callback, else None."""
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
        # Offer a quick shortcut into editing destinations too, instead of making
        # the user navigate back to task settings and tap in again.
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Edit Destinations", callback_data=f"task:edit-dest:{edit_task_id}")],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"set:task:{edit_task_id}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ])
        await _reply_or_edit(
            message_obj,
            safe_t(language, "sources_updated") if changed else safe_t(language, "generic_error"),
            keyboard, edit,
        )
        return None
    await state.update_data(sources=sources, destinations=[], picker_dialogs=None)
    await state.set_state(TaskStates.waiting_destination)
    await _render_chat_picker(message_obj, db, telethon, state, user_id, "dst", language, edit=edit)
    return None

async def _finish_destinations(message_obj, state: FSMContext, db: Database, telethon: TelethonService,
                               forwarding: ForwardingEngine, settings: Settings, user_id: int,
                               language: str, *, edit: bool = False) -> str | None:
    """Confirms destinations and creates (or updates) the task. Shared by /done and the Done button."""
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
            _nav_keyboard(back=f"set:task:{edit_task_id}"), edit,
        )
        return None
    task_id = await db.create_task_multi(user_id, str(data.get("task_name") or "Task"),
                                        list(data.get("sources") or []), destinations)
    await state.clear()
    await forwarding.refresh_task(task_id)
    # Admins still get the internal task id for support/debugging purposes.
    await _notify_admins(message_obj.bot, settings, f"➕ New task\nID: {task_id}\nUser: {user_id}")
    # The user themself should never see the raw internal task id — just the
    # confirmation and the name they picked.
    await _reply_or_edit(message_obj, safe_t(language, "task_created", task_name=safe_html(str(data.get("task_name") or "Task"))),
                         _nav_keyboard(), edit)
    return None

@router.callback_query(F.data.startswith("pick:"))
async def picker_callback(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService,
                          forwarding: ForwardingEngine, settings: Settings) -> None:
    """Handles the tap-to-select number keypad: pick:<src|dst|done|refresh>:<value>."""
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
        await _render_chat_picker(callback.message, db, telethon, state, callback.from_user.id,
                                  field, language, refresh=True, edit=True)
        return await callback.answer(safe_t(language, "picker_refreshed"))

    if action == "done":
        field = value if value in {"src", "dst"} else "src"
        if field == "src":
            toast = await _finish_sources(callback.message, state, db, telethon, forwarding,
                                          callback.from_user.id, language, edit=True)
        else:
            toast = await _finish_destinations(callback.message, state, db, telethon, forwarding,
                                               settings, callback.from_user.id, language, edit=True)
        return await callback.answer(toast or "")

    if action in {"src", "dst"}:
        if not value.isdigit():
            return await callback.answer()
        handled = await _picker_toggle_number(callback.message, state, db, telethon, callback.from_user.id,
                                              action, language, int(value), edit=True)
        if not handled:
            return await callback.answer(safe_t(language, "picker_expired"), show_alert=True)
        return await callback.answer()
    await callback.answer()

@router.message(TaskStates.waiting_source)
async def task_source(message: Message, state: FSMContext, db: Database, telethon: TelethonService, forwarding: ForwardingEngine) -> None:
    text = _text_or_forwarded_chat_id(message)
    if not text: return
    language = await _language_for_message(db, message)
    if text == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    data = await state.get_data()
    sources = list(data.get("sources", []))
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        return await _finish_sources(message, state, db, telethon, forwarding,
                                     message.from_user.id, language)

    # Typing a number still works as a fallback for the keypad above.
    if text.isdigit():
        handled = await _picker_toggle_number(message, state, db, telethon, message.from_user.id, "src", language, int(text))
        if handled:
            return
        return await message.answer(safe_t(language, "picker_bad_number"), parse_mode="HTML")

    if len(sources) >= plan.sources_per_task:
        return await message.answer(safe_t(language, "picker_limit_reached", limit=plan.sources_per_task), parse_mode="HTML")
    # Manual entry format validation (@name / t.me link / forwarded chat id)
    if not CHANNEL_INPUT_RE.match(text):
        return await message.answer(safe_t(language, "invalid_channel_format"), parse_mode="HTML")
    try:
        entity = await telethon.validate_for_user(message.from_user.id, text)
    except ValueError as exc:
        return await message.answer(f"⚠️ {safe_html(exc)}")
    if any(int(e.get("id", 0)) == int(entity.get("id", 0)) for e in sources):
        return await message.answer(safe_t(language, "picker_already_added"), parse_mode="HTML")
    sources.append(entity)
    await state.update_data(sources=sources)
    if len(sources) < plan.sources_per_task:
        await _render_chat_picker(message, db, telethon, state, message.from_user.id, "src", language)
    else:
        await _finish_sources(message, state, db, telethon, forwarding, message.from_user.id, language)

@router.message(TaskStates.waiting_destination)
async def task_destination(message: Message, state: FSMContext, db: Database, telethon: TelethonService, forwarding: ForwardingEngine, settings: Settings) -> None:
    text = _text_or_forwarded_chat_id(message)
    if not text: return
    language = await _language_for_message(db, message)
    if text == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    data = await state.get_data()
    destinations = list(data.get("destinations", []))
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        return await _finish_destinations(message, state, db, telethon, forwarding, settings,
                                          message.from_user.id, language)

    # Typing a number still works as a fallback for the keypad above.
    if text.isdigit():
        handled = await _picker_toggle_number(message, state, db, telethon, message.from_user.id, "dst", language, int(text))
        if handled:
            return
        return await message.answer(safe_t(language, "picker_bad_number"), parse_mode="HTML")

    if len(destinations) >= plan.destinations_per_task:
        return await message.answer(safe_t(language, "picker_limit_reached", limit=plan.destinations_per_task), parse_mode="HTML")
    # Manual entry format validation (@name / t.me link / forwarded chat id)
    if not CHANNEL_INPUT_RE.match(text):
        return await message.answer(safe_t(language, "invalid_channel_format"), parse_mode="HTML")
    try:
        destination = await telethon.validate_for_user(message.from_user.id, text)
    except ValueError as exc:
        return await message.answer(f"⚠️ {safe_html(exc)}")
    if any(int(e.get("id", 0)) == int(destination.get("id", 0)) for e in destinations):
        return await message.answer(safe_t(language, "picker_already_added"), parse_mode="HTML")
    destinations.append(destination)
    await state.update_data(destinations=destinations)
    if len(destinations) < plan.destinations_per_task:
        return await _render_chat_picker(message, db, telethon, state, message.from_user.id, "dst", language)
    await _finish_destinations(message, state, db, telethon, forwarding, settings,
                               message.from_user.id, language)

@router.message(Command("pause", "resume", "deletetask"))
async def task_action(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split()
    cmd = parts[0].lstrip("/").lower() if parts else ""
    language = await _language_for_message(db, message)

    # Bare command (no ID) -> open interactive task picker (no task ID typing required)
    if len(parts) == 1:
        tasks = await db.list_tasks(message.from_user.id)
        if cmd == "pause":
            targets = [t for t in tasks if not t["is_paused"]]
            title = "⏸️ <b>Pause a Task</b>\n\nSelect a task to pause:"
        elif cmd == "resume":
            targets = [t for t in tasks if t["is_paused"]]
            title = "▶️ <b>Resume a Task</b>\n\nSelect a task to resume:"
        else:  # deletetask
            targets = tasks
            title = "🗑️ <b>Delete a Task</b>\n\nSelect a task to delete:"

        if not targets:
            await message.answer(safe_t(language, "no_tasks_short"), reply_markup=_nav_keyboard())
            return

        rows = []
        for t in targets[:20]:
            label = f"{'⏸️' if t['is_paused'] else '▶️'} {safe_html(t['task_name'])}"
            prefix = "task:resume:" if cmd == "resume" else "task:pause:" if cmd == "pause" else "task:delete:"
            rows.append([InlineKeyboardButton(text=label, callback_data=f"{prefix}{t['id']}")])
        rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
        await message.answer(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        return

    # Explicit task ID path (backward compatibility)
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer(
            f"Usage: <code>/{cmd} &lt;task_id&gt;</code>\n\nOr send <code>/{cmd}</code> without ID to pick from buttons.",
            parse_mode="HTML",
        )
    task_id = int(parts[1])
    if cmd == "deletetask":
        changed = await db.delete_task(message.from_user.id, task_id)
        if changed: await forwarding.remove_task(task_id)
        await message.answer("🗑️ Deleted." if changed else "🗑️ Not found.")
    else:
        paused = cmd == "pause"
        changed = await db.set_task_paused(message.from_user.id, task_id, paused, "user" if paused else None)
        if changed: await forwarding.refresh_task(task_id)
        await message.answer("⏸️ Paused." if changed and paused else "▶️ Resumed." if changed else "⚠️ Not found.")

@router.callback_query(F.data.startswith("task:pause:") | F.data.startswith("task:resume:"))
async def task_pause_resume_cb(callback: CallbackQuery, db: Database, forwarding: ForwardingEngine) -> None:
    _, action, task_id_str = callback.data.split(":")
    task_id = int(task_id_str)
    paused = action == "pause"
    changed = await db.set_task_paused(callback.from_user.id, task_id, paused, "user" if paused else None)
    if changed: await forwarding.refresh_task(task_id)
    if callback.message: await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer(f"Task {action}d" if changed else "Not found")

@router.callback_query(F.data.startswith("task:delete:"))
async def task_delete_prompt(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    language = await _language_for_callback(db, callback)
    name = safe_html(task["task_name"])
    await callback.message.edit_text(
        safe_t(language, "delete_confirm", name=name),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Yes, Delete", callback_data=f"task:delete-confirm:{task_id}")],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="menu:tasks")]
        ]),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("task:delete-confirm:"))
async def task_delete_confirm_cb(callback: CallbackQuery, db: Database, forwarding: ForwardingEngine) -> None:
    task_id = int(callback.data.split(":")[2])
    # Re-verify ownership before deletion (safety)
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    changed = await db.delete_task(callback.from_user.id, task_id)
    if changed: await forwarding.remove_task(task_id)
    if callback.message: await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer("Deleted" if changed else "Not found")

@router.callback_query(F.data.startswith("task:edit-source:"))
async def edit_source_cb(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    if callback.message is None: return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    await state.set_state(TaskStates.waiting_source)
    # Existing channels shown first (pre-selected), then the top-20 recent chats
    # BUG FIX: task["sources"] comes back from asyncpg as a raw JSONB *string*,
    # not a Python list (no type codec registered on the pool) — the old
    # `isinstance(..., list)` check was always False, silently resetting the
    # selection to empty every time this screen opened.
    existing = json.loads(task["sources"]) if isinstance(task["sources"], str) else (task["sources"] or [])
    await state.update_data(edit_task_id=task_id, edit_field="sources", sources=list(existing), picker_dialogs=None)
    language = await _language_for_callback(db, callback)
    await _render_chat_picker(callback.message, db, telethon, state, callback.from_user.id, "src", language, edit=True)
    await callback.answer()

@router.callback_query(F.data.startswith("task:edit-dest:"))
async def edit_dest_cb(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    if callback.message is None: return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id:
        return await callback.answer("Not found", show_alert=True)
    await state.set_state(TaskStates.waiting_destination)
    existing = json.loads(task["destinations"]) if isinstance(task["destinations"], str) else (task["destinations"] or [])
    await state.update_data(edit_task_id=task_id, edit_field="destinations", destinations=list(existing), picker_dialogs=None)
    language = await _language_for_callback(db, callback)
    await _render_chat_picker(callback.message, db, telethon, state, callback.from_user.id, "dst", language, edit=True)
    await callback.answer()

# ==========================================
# DYNAMIC PLAN SETTINGS (/setting)
# ==========================================

TIER_FEATURES: dict[str, set[str]] = {
    "free": {"header", "footer"},
    "silver": {"header", "footer"},
    "gold": {"header", "footer", "blacklist", "whitelist"},
    "platinum": {"header", "footer", "blacklist", "whitelist",
                 "replace_usernames", "replace_links", "replace_words",
                 "watermark", "auto_delete_seconds", "user_filter", "attach_stored_file"},
}

# Feature -> (category callback suffix, required plan display name)
FEATURE_META: dict[str, tuple[str, str]] = {
    # Forwarding Controls
    "watermark":           ("fwd",  "Platinum"),
    "auto_delete_seconds": ("fwd",  "Platinum"),
    "user_filter":         ("fwd",  "Platinum"),
    "attach_stored_file":  ("fwd",  "Platinum"),
    # Filters & Replacements
    "header":              ("flt",  "Free"),
    "footer":              ("flt",  "Free"),
    "blacklist":           ("flt",  "Gold"),
    "whitelist":           ("flt",  "Gold"),
    "replace_usernames":   ("flt",  "Platinum"),
    "replace_links":       ("flt",  "Platinum"),
    "replace_words":       ("flt",  "Platinum"),
}

# Feature -> i18n key (used for confirmation message)
FEATURE_DISPLAY = {
    "header": "Header Text",
    "footer": "Footer Text",
    "blacklist": "Blacklist Words",
    "whitelist": "Whitelist Words",
    "replace_usernames": "Replace Usernames",
    "replace_links": "Replace Links",
    "replace_words": "Replace Words",
    "watermark": "Watermark",
    "watermark_text": "Watermark Text",
    "auto_delete_seconds": "Auto Delete",
    "user_filter": "Sender Filter",
    "attach_stored_file": "Attach Uploaded File",
}

@router.message(Command("setting", "settings"))
async def setting_command(message: Message, db: Database) -> None:
    tasks = await db.list_tasks(message.from_user.id)
    if not tasks:
        return await message.answer("📋 No tasks available. Create one first.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ New Task", callback_data="task:create")], [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]]))
    rows = [[InlineKeyboardButton(text=f"⚙️ {safe_html(t['task_name'])}", callback_data=f"set:task:{t['id']}")] for t in tasks]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    await message.answer("⚙️ <b>Select a task to configure:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@router.callback_query(F.data == "menu:settings")
async def menu_settings(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    tasks = await db.list_tasks(callback.from_user.id)
    if not tasks:
        return await callback.message.edit_text("📋 No tasks available. Create one first.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ New Task", callback_data="task:create")], [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]]))
    rows = [[InlineKeyboardButton(text=f"⚙️ {safe_html(t['task_name'])}", callback_data=f"set:task:{t['id']}")] for t in tasks]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    await callback.message.edit_text("⚙️ <b>Select a task to configure:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("set:task:"))
async def setting_task_menu(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    task_id = int(callback.data.split(":")[2])
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id: return await callback.answer("Not found", show_alert=True)

    sources = json.loads(task["sources"]) if isinstance(task["sources"], str) else (task["sources"] or [])
    destinations = json.loads(task["destinations"]) if isinstance(task["destinations"], str) else (task["destinations"] or [])
    src_names = ", ".join(safe_html(s.get("title") or s.get("id") or "?") for s in sources) or "—"
    dst_names = ", ".join(safe_html(d.get("title") or d.get("id") or "?") for d in destinations) or "—"
    status_line = "⏸️ Paused" if task["is_paused"] else "▶️ Active"

    rows = [
        [InlineKeyboardButton(text="🔀 Forwarding Controls", callback_data=f"set:cat:{task_id}:fwd")],
        [InlineKeyboardButton(text="🧹 Filters & Replacements", callback_data=f"set:cat:{task_id}:flt")],
        [InlineKeyboardButton(text="📥 Source/Target Channels", callback_data=f"set:cat:{task_id}:ch")],
        [InlineKeyboardButton(text="▶️ Resume" if task["is_paused"] else "⏸️ Pause",
                               callback_data=f"task:{'resume' if task['is_paused'] else 'pause'}:{task_id}")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="menu:settings"), InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]
    ]
    text = (
        f"⚙️ <b>Settings for:</b> {safe_html(task['task_name'])}\n"
        f"Status: {status_line}\n\n"
        f"📥 <b>Sources:</b> {src_names}\n"
        f"📤 <b>Destinations:</b> {dst_names}\n\n"
        f"Choose a category:"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await callback.answer()

def _get_setting_btn(label: str, feature: str, task_id: int, plan_name: str) -> InlineKeyboardButton:
    allowed = feature in TIER_FEATURES.get(plan_name, set())
    if not allowed: return InlineKeyboardButton(text=f"🔒 {label}", callback_data=f"set:lock:{feature}")
    return InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"set:edit:{task_id}:{feature}")

def _get_toggle_btn(label: str, feature: str, task_id: int, plan_name: str, current_settings: dict) -> InlineKeyboardButton:
    allowed = feature in TIER_FEATURES.get(plan_name, set())
    if not allowed: return InlineKeyboardButton(text=f"🔒 {label}", callback_data=f"set:lock:{feature}")
    val = current_settings.get(feature, False)
    status = "✅ On" if val else "❌ Off"
    return InlineKeyboardButton(text=f"{label} ({status})", callback_data=f"set:tog:{task_id}:{feature}:{'off' if val else 'on'}")

async def _render_settings_category(message_obj, db: Database, user_id: int, task_id: int, cat: str) -> None:
    """Renders a settings category screen. Safe to call from any handler
    (never mutates callback objects)."""
    task = await db.get_task(task_id)
    if not task:
        return
    user = await db.get_user(user_id)
    plan_name = str(user["plan"]) if user else "free"
    language = language_for(user["preferred_language"]) if user else "en"
    import json
    st = task["settings"] if isinstance(task["settings"], dict) else json.loads(task["settings"] or "{}")

    rows = []
    text = ""
    if cat == "fwd":
        text = "🔀 <b>Forwarding Controls</b>"
        rows.append([_get_toggle_btn("💧 Watermark", "watermark", task_id, plan_name, st)])
        rows.append([_get_setting_btn("🗑️ Auto Delete (secs)", "auto_delete_seconds", task_id, plan_name)])
        rows.append([_get_setting_btn("👤 Sender Filter", "user_filter", task_id, plan_name)])
        if plan_name == "platinum":
            # Default ON so existing platinum users keep their current behaviour
            # until they explicitly turn it off.
            st_with_default = dict(st)
            st_with_default.setdefault("attach_stored_file", True)
            rows.append([_get_toggle_btn("📎 Attach Uploaded File", "attach_stored_file", task_id, plan_name, st_with_default)])
            rows.append([InlineKeyboardButton(text="📤 Upload File", callback_data="menu:upload")])
    elif cat == "flt":
        text = "🧹 <b>Filters &amp; Replacements</b>"
        rows.append([_get_setting_btn("Blacklist Words", "blacklist", task_id, plan_name),
                     _get_setting_btn("Whitelist Words", "whitelist", task_id, plan_name)])
        rows.append([_get_setting_btn("Replace Usernames", "replace_usernames", task_id, plan_name),
                     _get_setting_btn("Replace Links", "replace_links", task_id, plan_name)])
        rows.append([_get_setting_btn("Replace Words", "replace_words", task_id, plan_name)])
        rows.append([_get_setting_btn("Header Text", "header", task_id, plan_name),
                     _get_setting_btn("Footer Text", "footer", task_id, plan_name)])
    elif cat == "ch":
        text = safe_t(language, "settings_cat_channels")
        rows.append([InlineKeyboardButton(text="📥 Sources", callback_data=f"task:edit-source:{task_id}"),
                     InlineKeyboardButton(text="📤 Destinations", callback_data=f"task:edit-dest:{task_id}")])

    rows.append([InlineKeyboardButton(text="◀️ Back", callback_data=f"set:task:{task_id}"), InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        with suppress(TelegramBadRequest):
            try:
                await message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
                return
            except TelegramBadRequest:
                pass
    await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")

@router.callback_query(F.data.startswith("set:cat:"))
async def setting_category(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    _, _, task_id_str, cat = callback.data.split(":")
    task_id = int(task_id_str)
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id: return await callback.answer("Not found", show_alert=True)
    await _render_settings_category(callback.message, db, callback.from_user.id, task_id, cat)
    await callback.answer()

@router.callback_query(F.data.startswith("set:lock:"))
async def setting_locked(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    feature = callback.data.split(":", 2)[2]
    _, required_plan = FEATURE_META.get(feature, ("msg", "Silver"))
    language = await _language_for_callback(db, callback)
    text = safe_t(language, "feature_locked", feature=FEATURE_DISPLAY.get(feature, feature), required_plan=required_plan)
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:settings")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("set:tog:"))
async def setting_toggle(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    _, _, task_id_str, feature, val_str = callback.data.split(":")
    task_id = int(task_id_str)
    user = await db.get_user(callback.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    if feature not in TIER_FEATURES.get(plan_name, set()): return await callback.answer("Locked feature", show_alert=True)
    val = val_str == "on"
    await db.update_task_settings(callback.from_user.id, task_id, {feature: val})
    cat, _ = FEATURE_META.get(feature, ("fwd", "Silver"))
    if callback.message:
        await _render_settings_category(callback.message, db, callback.from_user.id, task_id, cat)
    await callback.answer("✅ Updated")
    # Watermark turned ON -> immediately ask for the watermark text (sub-feature)
    if feature == "watermark" and val and callback.message:
        language = language_for(user["preferred_language"]) if user else "en"
        await state.set_state(SettingsFlow.waiting_value)
        await state.update_data(task_id=task_id, feature="watermark_text", cat="fwd")
        await callback.message.answer(
            f"💧 <b>Watermark Text</b>\n\n{safe_t(language, 'watermark_text_prompt')}",
            reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:fwd"),
            parse_mode="HTML",
        )

@router.callback_query(F.data.startswith("set:edit:"))
async def setting_edit_input(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.message is None: return
    _, _, task_id_str, feature = callback.data.split(":")
    task_id = int(task_id_str)

    user = await db.get_user(callback.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    # watermark_text is a sub-feature of watermark
    gate_feature = "watermark" if feature == "watermark_text" else feature
    if gate_feature not in TIER_FEATURES.get(plan_name, set()): return await callback.answer("Locked", show_alert=True)

    cat, _ = FEATURE_META.get(feature, ("fwd", "Silver"))

    await state.set_state(SettingsFlow.waiting_value)
    await state.update_data(task_id=task_id, feature=feature, cat=cat)

    language = await _language_for_callback(db, callback)
    prompt_key = {
        "header": "header_prompt",
        "footer": "footer_prompt",
        "replace_words": "replace_prompt",
        "replace_usernames": "replace_prompt",
        "replace_links": "replace_prompt",
        "blacklist": "blacklist_prompt",
        "whitelist": "whitelist_prompt",
        "auto_delete_seconds": "autodelete_prompt",
        "user_filter": "userfilter_prompt",
        "watermark_text": "watermark_text_prompt",
    }.get(feature)
    feature_label = FEATURE_DISPLAY.get(feature, feature.title())
    prompt_text = safe_t(language, prompt_key) if prompt_key else "Enter value:"

    # Show current value above the prompt
    import json
    task = await db.get_task(task_id)
    st = task["settings"] if isinstance(task["settings"], dict) else (json.loads(task["settings"] or "{}") if task else {})
    cur_val = st.get(feature)
    current_line = ""
    if feature in ("blacklist", "whitelist", "user_filter"):
        shown = ", ".join(str(x) for x in (cur_val or [])) or "—"
        current_line = f"\n\n<b>Current:</b> {safe_html(shown)}"
    elif feature in ("header", "footer", "watermark_text"):
        shown = str(cur_val) if cur_val else "—"
        current_line = f"\n\n<b>Current:</b> {safe_html(shown)}"
    elif feature in ("replace_words", "replace_usernames", "replace_links"):
        if isinstance(cur_val, dict) and cur_val:
            shown = ", ".join(f"{safe_html(k)} = {safe_html(v)}" for k, v in list(cur_val.items())[:10])
        else:
            shown = "—"
        current_line = f"\n\n<b>Current:</b> {shown}"
    elif feature == "auto_delete_seconds":
        current_line = f"\n\n<b>Current:</b> {cur_val if cur_val else 'Off'}"

    await callback.message.edit_text(
        f"✏️ <b>{safe_html(feature_label)}</b>\n\n{prompt_text}{current_line}",
        reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"),
        parse_mode="HTML",
    )
    await callback.answer()

@router.message(SettingsFlow.waiting_value)
async def setting_save_value(message: Message, state: FSMContext, db: Database, forwarding: ForwardingEngine) -> None:
    if not message.text: return
    data = await state.get_data()
    task_id = data["task_id"]
    feature = data["feature"]
    cat = data["cat"]
    val = message.text.strip()
    language = await _language_for_message(db, message)

    clear = val.lower() == "/clear"

    update_val = None
    cleared = False
    if feature in ("header", "footer", "watermark_text"):
        update_val = "" if clear else val
        cleared = clear
    elif feature in ("blacklist", "whitelist"):
        if clear:
            update_val = []
            cleared = True
        else:
            update_val = [w.strip() for w in val.split(",") if w.strip()]
            if not update_val:
                return await message.answer(safe_t(language, "validation_invalid"), reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"))
    elif feature == "user_filter":
        if clear:
            update_val = []
            cleared = True
        else:
            # Accepts numeric Telegram IDs and @usernames (e.g. 123456, @dealkoti)
            entries = [w.strip() for w in val.split(",") if w.strip()]
            parsed: list[object] = []
            for entry in entries:
                if entry.lstrip("-").isdigit():
                    parsed.append(int(entry))
                elif re.match(r"^@[A-Za-z0-9_]{3,64}$", entry):
                    parsed.append(entry)
                else:
                    parsed = []
                    break
            if not parsed:
                return await message.answer(safe_t(language, "setting_invalid_ids"), reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"))
            update_val = parsed
    elif feature == "auto_delete_seconds":
        if clear:
            update_val = 0
            cleared = True
        else:
            try:
                update_val = int(val)
                if update_val < 0:
                    raise ValueError
            except ValueError:
                return await message.answer(safe_t(language, "setting_invalid_number"), reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"))
    elif feature in ("replace", "replace_words", "replace_usernames", "replace_links"):
        if clear:
            update_val = {}
            cleared = True
        else:
            mapping: dict[str, str] = {}
            for pair in val.split(","):
                if "=" in pair:
                    o, n = pair.split("=", 1)
                    o_s, n_s = o.strip(), n.strip()
                    if o_s and n_s:
                        mapping[o_s] = n_s
            if not mapping:
                return await message.answer(safe_t(language, "setting_invalid_replace"), reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"))
            update_val = mapping

    await db.update_task_settings(message.from_user.id, task_id, {feature: update_val})
    await forwarding.refresh_task(task_id)
    await state.clear()
    feature_label = FEATURE_DISPLAY.get(feature, feature)
    success_key = "setting_cleared" if cleared else "setting_saved"
    await message.answer(
        safe_t(language, success_key, feature=feature_label),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back to Category", callback_data=f"set:cat:{task_id}:{cat}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ])
    )

# ==========================================
# ADMIN COMMANDS
# ==========================================

@router.message(Command("stats"))
async def stats_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    stats = await db.stats()
    await message.answer("📊 <b>Admin Stats</b>\n\n" + "\n".join(f"<b>{k.replace('_', ' ').title()}:</b> {v}" for k, v in stats.items()), parse_mode="HTML", reply_markup=admin_keyboard())

def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Stats", callback_data="admin:stats"), InlineKeyboardButton(text="📣 Broadcast", callback_data="admin:broadcast:start")],
        [InlineKeyboardButton(text="📅 Weekly Report", callback_data="admin:weekly"), InlineKeyboardButton(text="👥 Recent Users", callback_data="admin:users")],
        [InlineKeyboardButton(text="👤 User Info", callback_data="admin:userinfo:start"), InlineKeyboardButton(text="🎁 Grant Days", callback_data="admin:grantpicker")],
        [InlineKeyboardButton(text="🏠 User Menu", callback_data="menu:home")],
    ])

async def _resolve_target_user(db: Database, raw: str) -> int | None:
    raw = raw.strip()
    if raw.lstrip("-").isdigit(): return int(raw)
    user = await db.get_user_by_username(raw)
    return int(user["telegram_user_id"]) if user is not None else None

@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    if callback.message: await callback.message.edit_text("🛠️ <b>Admin Dashboard</b>", parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()

@router.message(Command("admin"))
async def admin_dashboard(message: Message, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    await message.answer("🛠️ <b>Admin Dashboard</b>", parse_mode="HTML", reply_markup=admin_keyboard())

@router.callback_query(F.data == "admin:stats")
async def admin_stats_callback(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    stats = await db.stats()
    await callback.message.edit_text("📊 <b>Stats</b>\n\n" + "\n".join(f"<b>{k.replace('_', ' ').title()}:</b> {v}" for k, v in stats.items()), parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()

async def _weekly_report(db: Database) -> str:
    stats = await db.stats()
    return f"📅 <b>Weekly Report</b>\n\nUsers: {stats['users']}\nNew today: {stats['new_users_today']}\nPaid: {stats['paid_users']}\nActive tasks: {stats['active_tasks']}\nCaptured payments: {stats['captured_payments']}"

@router.callback_query(F.data == "admin:weekly")
async def weekly_report_callback(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    await callback.message.edit_text(await _weekly_report(db), parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin:users")
async def recent_users_callback(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    users = await db.list_users(15)
    lines = ["👥 <b>Recent Users</b>\n"]
    for u in users: lines.append(f"{u['telegram_user_id']} — {safe_html(u['first_name'] or u['username'] or 'No name')} — {str(u['plan']).title()}")
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()

@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    await state.set_state(AdminBroadcastStates.waiting_message)
    await message.answer("📣 Send broadcast message. /back to cancel.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")]]))

@router.message(AdminBroadcastStates.waiting_message)
async def broadcast_message(message: Message, state: FSMContext, settings: Settings) -> None:
    if not message.text: return
    if message.text.strip() == "/back":
        await state.clear()
        return await message.answer("Broadcast cancelled.")
    await state.update_data(broadcast_text=message.text[:4000])
    await message.answer(
        "📣 <b>Preview</b>\n\n" + safe_html(message.text[:4000]) + "\n\nChoose audience:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="All users", callback_data="admin:broadcast:all"), InlineKeyboardButton(text="Active users", callback_data="admin:broadcast:active")],
            [InlineKeyboardButton(text="Paid users", callback_data="admin:broadcast:paid"), InlineKeyboardButton(text="English", callback_data="admin:broadcast:english")],
            [InlineKeyboardButton(text="Hinglish", callback_data="admin:broadcast:hinglish"), InlineKeyboardButton(text="👥 Select Users", callback_data="admin:broadcast:selectusers")],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")]
        ])
    )

async def _run_broadcast(callback: CallbackQuery, state: FSMContext, db: Database, audience: str, users: list) -> None:
    """Shared send loop for both the audience-based and the manually-picked-users
    broadcast paths."""
    data = await state.get_data()
    text = str(data.get("broadcast_text", "")).strip()
    if not text: return await callback.answer("Missing text", show_alert=True)
    broadcast_id = await db.create_broadcast(callback.from_user.id, audience, text, len(users))
    sent = failed = blocked = 0
    await callback.message.edit_text(f"📣 Sending to {len(users)} users…", reply_markup=None)
    for i, u in enumerate(users, 1):
        try:
            await callback.bot.send_message(int(u["telegram_user_id"]), text)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
            await db.mark_user_inactive(int(u["telegram_user_id"]))
        except TelegramBadRequest: failed += 1
        if i % 20 == 0: await asyncio.sleep(1)
    await db.finish_broadcast(broadcast_id, sent, failed, blocked)
    await state.clear()
    await callback.message.edit_text(f"✅ Complete\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}", reply_markup=admin_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("admin:broadcast:"))
async def broadcast_send(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    audience = callback.data.rsplit(":", 1)[1]
    if audience == "start":
        await state.set_state(AdminBroadcastStates.waiting_message)
        await callback.message.edit_text("📣 Send broadcast message. /back to cancel.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")]]))
        return await callback.answer()
    if audience == "selectusers":
        return await _render_broadcast_user_picker(callback, db, state)
    users = await db.list_broadcast_users(audience)
    await _run_broadcast(callback, state, db, audience, users)

async def _render_broadcast_user_picker(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    """Recent active users shown as a toggle-able multi-select list for
    'Select Users' broadcasts — same tap-to-toggle pattern as the chat picker."""
    if callback.message is None: return
    data = await state.get_data()
    selected: list[int] = list(data.get("broadcast_selected_ids") or [])
    users = await db.list_recent_active_users(6)
    if not users:
        await callback.message.edit_text("No recent active users found.", reply_markup=admin_keyboard())
        return await callback.answer()
    rows = []
    for u in users:
        uid = int(u["telegram_user_id"])
        label = safe_html(u["first_name"] or u["username"] or str(uid))
        mark = "✅ " if uid in selected else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{label} ({u['plan']})", callback_data=f"admin:selu:toggle:{uid}")])
    rows.append([InlineKeyboardButton(text=f"✅ Send to {len(selected)} selected", callback_data="admin:selu:done")])
    rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")])
    await callback.message.edit_text(
        f"👥 <b>Select users ({len(selected)} picked):</b>\nTap a name to select/deselect.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("admin:selu:toggle:"))
async def broadcast_select_user_toggle(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
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
async def broadcast_select_user_done(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    data = await state.get_data()
    selected: list[int] = list(data.get("broadcast_selected_ids") or [])
    if not selected:
        return await callback.answer("Select at least one user first.", show_alert=True)
    users = await db.list_users_by_ids(selected)
    await _run_broadcast(callback, state, db, "selected", users)

async def _recent_users_picker(message: Message, db: Database, action: str, title: str) -> None:
    """Shows the 6 most recently active users as inline buttons for an admin action."""
    users = await db.list_recent_active_users(6)
    if not users:
        return await message.answer("No users found.")
    rows = []
    for u in users:
        label = safe_html(u["first_name"] or u["username"] or str(u["telegram_user_id"]))
        rows.append([InlineKeyboardButton(text=f"👤 {label} ({u['plan']})", callback_data=f"admin:{action}:{u['telegram_user_id']}")])
    rows.append([InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")])
    await message.answer(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

async def _recent_users_picker_edit(callback: CallbackQuery, db: Database, action: str, title: str) -> None:
    """Same as _recent_users_picker but edits the existing admin-panel message
    in place, for use from inline admin-panel buttons."""
    users = await db.list_recent_active_users(6)
    if callback.message is None: return
    if not users:
        await callback.message.edit_text("No users found.", reply_markup=admin_keyboard())
        return await callback.answer()
    rows = []
    for u in users:
        label = safe_html(u["first_name"] or u["username"] or str(u["telegram_user_id"]))
        rows.append([InlineKeyboardButton(text=f"👤 {label} ({u['plan']})", callback_data=f"admin:{action}:{u['telegram_user_id']}")])
    rows.append([InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")])
    await callback.message.edit_text(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "admin:grantpicker")
async def admin_grant_picker_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    await _recent_users_picker_edit(callback, db, "grant", "🎁 <b>Grant days to:</b>")

def _user_info_card(u) -> tuple[str, InlineKeyboardMarkup]:
    label = safe_html(u["first_name"] or u["username"] or "No name")
    block_label = "✅ Unblock" if u["is_blocked"] else "⛔ Block"
    block_action = "unblock" if u["is_blocked"] else "block"
    text = f"👤 {label}\nID: {u['telegram_user_id']}\nPlan: {u['plan']}\nExpiry: {u['plan_expiry']}\nBlocked: {'Yes' if u['is_blocked'] else 'No'}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Grant Days", callback_data=f"admin:grant:{u['telegram_user_id']}"),
         InlineKeyboardButton(text=block_label, callback_data=f"admin:{block_action}:{u['telegram_user_id']}")],
        [InlineKeyboardButton(text="🏠 Admin", callback_data="admin:home")],
    ])
    return text, keyboard

@router.callback_query(F.data == "admin:userinfo:start")
async def admin_userinfo_picker_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    await _recent_users_picker_edit(callback, db, "uinfo", "👤 <b>View info for:</b>")

@router.callback_query(F.data.startswith("admin:uinfo:"))
async def admin_userinfo_show_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    if callback.message is None: return
    target_user_id = int(callback.data.rsplit(":", 1)[1])
    user = await db.get_user(target_user_id)
    if not user:
        await callback.message.edit_text("⚠️ Not found.", reply_markup=admin_keyboard())
        return await callback.answer()
    text, keyboard = _user_info_card(user)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()

@router.message(Command("block", "unblock"))
async def block_user_command(message: Message, db: Database, settings: Settings, forwarding: ForwardingEngine) -> None:
    if not _is_admin(settings, message.from_user.id): return
    parts = (message.text or "").split()
    # Bare command -> inline user picker (6 most recent active users)
    if len(parts) == 1:
        cmd = parts[0].lstrip("/").lower()
        action = "unblock" if cmd == "unblock" else "block"
        title = f"👥 <b>Select user to {action}:</b>"
        return await _recent_users_picker(message, db, action, title)
    if len(parts) != 2: return await message.answer("Usage: /block &lt;telegram_user_id or @username&gt;\nOr send /block without args to pick from buttons.", parse_mode="HTML")
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None: return await message.answer("⚠️ User not found.")
    blocked = parts[0].lower() == "/block"
    changed = await db.set_blocked(user_id, blocked)
    if blocked: await forwarding.remove_user(user_id)
    else: await forwarding.refresh_user(user_id)
    await message.answer("✅ Updated." if changed else "⚠️ Not found.")

@router.message(Command("grantdays"))
async def grant_days_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    parts = (message.text or "").split()
    # Bare command -> inline user picker
    if len(parts) == 1:
        return await _recent_users_picker(message, db, "grant", "🎁 <b>Grant days to:</b>")
    if len(parts) not in (3, 4) or not parts[2].isdigit(): return await message.answer("Usage: /grantdays &lt;user&gt; &lt;days&gt; [plan]\nOr send /grantdays without args to pick from buttons.", parse_mode="HTML")
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None: return await message.answer("⚠️ User not found.")
    if len(parts) == 4:
        plan_key = parts[3].lower()
        if plan_key not in PLANS or plan_key == "free": return await message.answer("⚠️ Invalid premium plan.")
    else:
        target = await db.get_user(user_id)
        plan_key = str(target["plan"]) if target and target["plan"] != "free" else "silver"
    changed = await db.set_plan(user_id, plan_key, int(parts[2]))
    await message.answer(f"✅ {plan_key} granted." if changed else "⚠️ Not found.")

# NOTE: /setplan was removed — it duplicated /grantdays (both set plan+days on a
# user). /grantdays with an explicit plan argument covers the same case:
# "/grantdays <user> <days> <plan>".

@router.message(Command("listusers"))
async def list_users_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    users = await db.list_users(15)
    if not users: return await message.answer("No users found.")
    for u in users:
        text, keyboard = _user_info_card(u)
        await message.answer(text, reply_markup=keyboard)

@router.callback_query(F.data.startswith("admin:grant:"))
async def admin_grant_pick_plan(callback: CallbackQuery, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    target_user_id = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text(f"🎁 Grant plan to {target_user_id}?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=plan.name, callback_data=f"admin:grantplan:{target_user_id}:{key}") for key, plan in PLANS.items() if key != "free"],
        [InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")]
    ]))
    await callback.answer()

@router.callback_query(F.data.startswith("admin:grantplan:"))
async def admin_grant_pick_days(callback: CallbackQuery, settings: Settings, state: FSMContext) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    _, _, target_user_id, plan_key = callback.data.split(":")
    await state.set_state(AdminStates.waiting_grant_days)
    await state.update_data(target_user_id=int(target_user_id), plan=plan_key)
    await callback.message.edit_text("📅 How many days? (Send a number)")
    await callback.answer()

@router.message(AdminStates.waiting_grant_days)
async def admin_grant_days_finish(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    if not message.text or not message.text.strip().isdigit(): return await message.answer("⚠️ Send a valid number.")
    data = await state.get_data()
    changed = await db.set_plan(int(data["target_user_id"]), str(data["plan"]), int(message.text.strip()))
    await state.clear()
    await message.answer(f"✅ Granted." if changed else "⚠️ Not found.", reply_markup=admin_keyboard())

@router.callback_query(F.data.startswith("admin:block:") | F.data.startswith("admin:unblock:"))
async def admin_block_toggle(callback: CallbackQuery, db: Database, forwarding: ForwardingEngine, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    action, target_user_id = callback.data.split(":")[1], int(callback.data.split(":")[2])
    blocked = action == "block"
    changed = await db.set_blocked(target_user_id, blocked)
    if blocked: await forwarding.remove_user(target_user_id)
    else: await forwarding.refresh_user(target_user_id)
    await callback.answer("Updated" if changed else "Not found", show_alert=True)

@router.message(Command("referralpayout"))
async def referral_payout_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 2: return await message.answer("Usage: /referralpayout &lt;user&gt;")
    user_id = await _resolve_target_user(db, parts[1])
    result = await db.mark_referral_paid(user_id) if user_id else None
    if not result: return await message.answer("⚠️ No unpaid commission.")
    await message.answer(f"✅ Marked paid. Referrer: {result['referrer_id']}, Amount: {format_paise(int(result['commission_amount_paise']))}")

@router.message(Command("userinfo"))
async def user_info_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) == 1:
        return await _recent_users_picker(message, db, "uinfo", "👤 <b>View info for:</b>")
    if len(parts) != 2: return await message.answer("Usage: /userinfo &lt;user&gt;\nOr send /userinfo without args to pick from buttons.", parse_mode="HTML")
    user_id = await _resolve_target_user(db, parts[1])
    user = await db.get_user(user_id) if user_id else None
    if not user: return await message.answer("⚠️ Not found.")
    text, keyboard = _user_info_card(user)
    await message.answer(text, reply_markup=keyboard)

@router.message()
async def fallback(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(safe_t(language, "unknown_command"))

def _bot_commands() -> list[BotCommand]:
    return [BotCommand(command=cmd.removeprefix("/"), description=desc[:256]) for cmd, desc, _ in USER_COMMANDS]

def _admin_bot_commands() -> list[BotCommand]:
    return [BotCommand(command=cmd.removeprefix("/"), description=desc[:256]) for cmd, desc in ADMIN_COMMANDS]

# ==========================================
# FASTAPI & WEBHOOKS
# ==========================================

def build_app(bot: Bot, db: Database, settings: Settings, billing: RazorpayBilling, forwarding: ForwardingEngine) -> FastAPI:
    app = FastAPI(title="Dealskoti Forwarder", version="0.1.0", docs_url=None, redoc_url=None)

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
        try:
            payload = billing.parse_json(raw_body)
            captured = billing.parse_captured_payment(payload)
            if captured is None:
                return JSONResponse({"status": "ignored"})

            stored_payment = None
            if captured.order_id:
                stored_payment = await db.get_payment_for_order(captured.order_id)

            # Fallback: `payment.captured` cannot carry the payment-link id, so
            # recover the order from the notes we attached when creating the link.
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
                duration_days(stored_cycle), stored_plan, stored_cycle
            )

        except (BillingError, ValueError) as exc:
            logger.warning("Rejected webhook: %s", str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception:
            logger.exception("Webhook processing failed")
            return JSONResponse({"error": "internal error"}, status_code=500)

        if user_id is not None:
            # Hot-reload the engine so the new plan's limits/features apply at once.
            with suppress(Exception):
                await forwarding.refresh_user(user_id)
            user = await db.get_user(user_id)
            if user is not None:
                language = language_for(user["preferred_language"])
                expiry_str = user["plan_expiry"].astimezone(ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %I:%M %p IST") if user["plan_expiry"] else "—"
                with suppress(Exception):
                    await bot.send_message(user_id, safe_t(language, "payment_success", plan=stored_plan.title(), days=duration_days(stored_cycle), amount=format_paise(captured.amount_paise), txn_id=captured.payment_id, expiry=expiry_str))
                await _notify_admins(bot, settings, f"✅ Verified Payment\nUser: {user_id}\nPlan: {stored_plan.title()}\nAmount: {format_paise(captured.amount_paise)}")
        return JSONResponse({"status": "processed"})

    return app

# ==========================================
# MONITORS & ENTRYPOINT
# ==========================================

async def _membership_monitor(bot: Bot, db: Database, settings: Settings, forwarding: ForwardingEngine) -> None:
    while True:
        try:
            for row in await db.get_users_for_membership_check():
                user_id = int(row["telegram_user_id"])
                member = await user_is_member(bot, settings, user_id)
                user = await db.get_user(user_id)
                if user is None or bool(user["updates_channel_member"]) == member: continue
                await db.set_membership(user_id, member)
                if member:
                    await db.resume_channel_gate_tasks(user_id)
                    await forwarding.refresh_user(user_id)
                else:
                    await db.mark_channel_gate_paused_tasks(user_id)
                    await forwarding.remove_user(user_id)
        except asyncio.CancelledError: raise
        except Exception: logger.exception("Membership monitor iteration failed")
        await asyncio.sleep(300)

async def _run(settings: Settings) -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db = Database(settings.database_url)
    await db.connect()
    
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode="HTML"))
    telethon = TelethonService(settings, db)
    billing = RazorpayBilling(settings)
    forwarding = ForwardingEngine(db, telethon, settings.max_concurrent_forward_tasks)
    
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    # SAFETY: clear any stale webhook so polling doesn't conflict on Replit/Railway restart
    with suppress(Exception):
        await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_my_commands(_bot_commands())
    for admin_id in settings.admin_telegram_ids:
        await bot.set_my_commands(_bot_commands() + _admin_bot_commands(), scope=BotCommandScopeChat(chat_id=admin_id))

    api = build_app(bot, db, settings, billing, forwarding)
    server = uvicorn.Server(uvicorn.Config(api, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), log_level="info"))
    
    dispatcher_task = asyncio.create_task(dispatcher.start_polling(bot, db=db, settings=settings, telethon=telethon, billing=billing, forwarding=forwarding))
    server_task = asyncio.create_task(server.serve())
    membership_task = asyncio.create_task(_membership_monitor(bot, db, settings, forwarding))
    
    try: timezone = ZoneInfo(settings.default_timezone)
    except Exception: timezone = ZoneInfo("UTC")
    scheduler = AsyncIOScheduler(timezone=timezone)

    async def send_weekly_report(): await _notify_admins(bot, settings, await _weekly_report(db))
    async def send_expiry_reminders():
        for days_ahead in (3, 1):
            for row in await db.get_expiring_users(days_ahead):
                with suppress(Exception): await bot.send_message(int(row["telegram_user_id"]), f"⏳ Your {str(row['plan']).title()} plan expires in {days_ahead} day(s). Use /plans to renew.")
    async def downgrade_expired_plans():
        downgraded = await db.downgrade_expired_users()
        for row in downgraded:
            with suppress(Exception): await bot.send_message(int(row["telegram_user_id"]), "❌ Your plan has expired and you've been downgraded to Free. Use /plans to resubscribe.")

    scheduler.add_job(send_weekly_report, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=timezone), replace_existing=True)
    scheduler.add_job(send_expiry_reminders, CronTrigger(hour=10, minute=0, timezone=timezone), replace_existing=True)
    scheduler.add_job(downgrade_expired_plans, CronTrigger(hour="*", minute=5, timezone=timezone), replace_existing=True)
    scheduler.start()
    
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
        if "forwarding_task" in locals(): forwarding_task.cancel()
        await forwarding.stop()
        await telethon.cancel_all_logins()
        with suppress(asyncio.CancelledError): await dispatcher_task
        with suppress(asyncio.CancelledError): await membership_task
        if "forwarding_task" in locals():
            with suppress(asyncio.CancelledError): await forwarding_task
        await db.close()
        await bot.session.close()

def run() -> None:
    try: settings = Settings.from_env()
    except ConfigurationError as exc: raise SystemExit(str(exc)) from exc
    asyncio.run(_run(settings))
