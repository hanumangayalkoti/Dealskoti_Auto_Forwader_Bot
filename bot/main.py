from __future__ import annotations

import asyncio
import html
import logging
import os
import traceback
from contextlib import suppress
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    ErrorEvent,
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
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
from .db import Database
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
from .plans import PLANS, duration_days, format_paise, payable_amount_paise
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

def safe_html(text: str) -> str:
    """Safely escape text for HTML parsing to prevent UI breaks."""
    return html.escape(str(text))

def safe_t(lang: str, key: str, **kwargs) -> str:
    try:
        return t(lang, key, **kwargs)
    except KeyError:
        return f"[{key}]"

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
            InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks"),
        ],
        [
            InlineKeyboardButton(text="➕ New Task", callback_data="task:create"),
            InlineKeyboardButton(text="⚙️ Settings", callback_data="menu:settings"),
        ],
        [
            InlineKeyboardButton(text="👤 My Account", callback_data="menu:account"),
            InlineKeyboardButton(text="💎 Plans", callback_data="menu:plans"),
        ],
        [
            InlineKeyboardButton(text="❓ Help / FAQ", callback_data="faq:page:1"),
            InlineKeyboardButton(text="🌐 Language", callback_data="language:choose"),
        ],
        [
            InlineKeyboardButton(text="📞 Support", url=settings.support_bot_link or "https://t.me/support"),
            InlineKeyboardButton(text="📢 Updates", url=f"https://t.me/{settings.update_channel_username.lstrip('@')}"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def faq_accordion_keyboard(language: str, page: int, expanded_index: int = -1) -> InlineKeyboardMarkup:
    faqs = FAQS[language_for(language)]
    total_pages = (len(faqs) + 4) // 5
    page = max(1, min(page, total_pages))
    start = (page - 1) * 5
    
    rows = []
    for index, faq in enumerate(faqs[start : start + 5]):
        actual_index = start + index
        if actual_index == expanded_index:
            rows.append([InlineKeyboardButton(text=f"🔽 {faq.question}", callback_data=f"faq:collapse:{page}")])
        else:
            rows.append([InlineKeyboardButton(text=f"▶️ {faq.question}", callback_data=f"faq:expand:{page}:{actual_index}")])
            
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Prev", callback_data=f"faq:page:{page - 1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="Next ▶️", callback_data=f"faq:page:{page + 1}"))
    if nav_buttons:
        rows.append(nav_buttons)
        
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

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

def _format_name(user) -> str:
    return safe_html(user["first_name"] or user["username"] or user["telegram_user_id"])

async def _render_tasks(message: Message, db: Database, user_id: int) -> None:
    user = await db.get_user(user_id)
    language = language_for(user["preferred_language"]) if user else "en"
    tasks = await db.list_tasks(user_id)
    
    rows: list[list[InlineKeyboardButton]] = []
    if tasks:
        lines = [safe_t(language, "tasks_title")]
        for task in tasks:
            status = "⏸️ Paused" if task["is_paused"] else "▶️ Active"
            safe_task_name = safe_html(task['task_name'])
            lines.append(f"#{task['id']} — {safe_task_name} — {status}")
            
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
        text = safe_t(language, "no_tasks_short")
        
    rows.append([InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    # Smart edit to avoid Message can't be edited error
    if message.from_user and message.from_user.is_bot:
        await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")

# ==========================================
# GLOBAL / CORE COMMANDS
# ==========================================

@router.message(Command("start"))
async def start(message: Message, db: Database, settings: Settings) -> None:
    user, is_new = await db.ensure_user_with_status(message.from_user.id, message.from_user.username, message.from_user.first_name)
    if is_new and await db.mark_new_user_notified(message.from_user.id):
        await _notify_admins(
            message.bot, settings,
            f"🆕 New Dealskoti user\nName: {safe_html(message.from_user.full_name)}\nUsername: @{message.from_user.username or '—'}\nID: <code>{message.from_user.id}</code>"
        )
    language = language_for(user["preferred_language"])
    if not user["language_selected"]:
        await message.answer("🌐 Choose your language / Apni language choose karo:", reply_markup=language_keyboard())
        return
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    await message.answer(safe_t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))

@router.message(Command("menu"))
async def menu_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        await message.answer(safe_t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))

@router.message(Command("help"))
async def help_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    text = safe_t(language, "help_title", commands=command_help(language))
    if _is_admin(settings, message.from_user.id):
        text += "\n\n" + safe_t(language, "admin_help_title", commands=admin_help())
    await message.answer(text, reply_markup=_nav_keyboard())

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
    await message.answer(safe_t(language, "support", link=settings.support_bot_link or "support"), reply_markup=_nav_keyboard())

@router.message(Command("updates", "channel"))
async def updates_command(message: Message, settings: Settings) -> None:
    await message.answer(
        "📢 Updates Channel",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Join Updates Channel", url=f"https://t.me/{settings.update_channel_username.lstrip('@')}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]
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
        await callback.message.answer(safe_t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))
    await callback.answer()

# ==========================================
# FAQ FLOW (ACCORDION)
# ==========================================

@router.message(Command("faq"))
async def faq_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(safe_t(language, "faq_title", page=1, pages=3), reply_markup=faq_accordion_keyboard(language, 1, -1))

@router.callback_query(F.data.startswith("faq:page:"))
async def faq_page(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    page = int(callback.data.split(":")[2])
    await callback.message.edit_text(safe_t(language, "faq_title", page=page, pages=3), reply_markup=faq_accordion_keyboard(language, page, -1))
    await callback.answer()

@router.callback_query(F.data.startswith("faq:expand:"))
async def faq_expand(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    _, _, page_str, index_str = callback.data.split(":")
    page, index = int(page_str), int(index_str)
    faq = FAQS[language][index]
    text = safe_t(language, "faq_answer", question=safe_html(faq.question), answer=safe_html(faq.answer))
    await callback.message.edit_text(text, reply_markup=faq_accordion_keyboard(language, page, index))
    await callback.answer()

@router.callback_query(F.data.startswith("faq:collapse:"))
async def faq_collapse(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    page = int(callback.data.split(":")[2])
    await callback.message.edit_text(safe_t(language, "faq_title", page=page, pages=3), reply_markup=faq_accordion_keyboard(language, page, -1))
    await callback.answer()

# ==========================================
# MAIN MENU NAVIGATION
# ==========================================

@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    if callback.message is None: return
    language = await _language_for_callback(db, callback)
    if await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.message.edit_text(safe_t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))
    await callback.answer()

@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    language = await _language_for_callback(db, callback)
    if callback.message:
        await callback.message.edit_text(safe_t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))
    await callback.answer("Cancelled", show_alert=False)

# ==========================================
# ACCOUNT & LOGIN
# ==========================================

@router.message(Command("account", "myaccount"))
async def account_command(message: Message, db: Database) -> None:
    user = await _ensure_user(db, message)
    language = language_for(user["preferred_language"])
    session = "connected" if await db.has_active_session(message.from_user.id) else "not connected"
    ist_zone = ZoneInfo("Asia/Kolkata")
    expiry = user["plan_expiry"].astimezone(ist_zone).strftime("%d %b %Y, %I:%M %p IST") if user["plan_expiry"] else "Lifetime"
    tasks = await db.count_tasks(message.from_user.id)
    usage = await db.daily_usage(message.from_user.id)
    
    await message.answer(
        safe_t(
            language, "account_details",
            name=_format_name(user),
            username=f"@{user['username']}" if user["username"] else "—",
            user_id=user["telegram_user_id"],
            plan=str(user["plan"]).title(),
            expiry=expiry,
            payment="Active" if user["plan"] != "free" else "No paid payment",
            session=session,
            tasks=tasks,
            forwarding=f"{usage} messages today",
            membership="Verified" if user["updates_channel_member"] else "Not verified",
            user_language=user["preferred_language"],
        ),
        reply_markup=_nav_keyboard(),
    )

@router.callback_query(F.data == "menu:account")
async def menu_account(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    language = language_for(user["preferred_language"])
    session = "connected" if await db.has_active_session(callback.from_user.id) else "not connected"
    ist_zone = ZoneInfo("Asia/Kolkata")
    expiry = user["plan_expiry"].astimezone(ist_zone).strftime("%d %b %Y, %I:%M %p IST") if user["plan_expiry"] else "Lifetime"
    tasks = await db.count_tasks(callback.from_user.id)
    usage = await db.daily_usage(callback.from_user.id)
    
    await callback.message.edit_text(
        safe_t(
            language, "account_details",
            name=_format_name(user),
            username=f"@{user['username']}" if user["username"] else "—",
            user_id=user["telegram_user_id"],
            plan=str(user["plan"]).title(),
            expiry=expiry,
            payment="Active" if user["plan"] != "free" else "No paid payment",
            session=session,
            tasks=tasks,
            forwarding=f"{usage} messages today",
            membership="Verified" if user["updates_channel_member"] else "Not verified",
            user_language=user["preferred_language"],
        ),
        reply_markup=_nav_keyboard(),
    )
    await callback.answer()

@router.message(Command("disconnect"))
async def disconnect_command(message: Message, db: Database, telethon: TelethonService, forwarding: ForwardingEngine, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    await message.answer(
        "⚠️ Are you sure you want to disconnect your Telegram session? Your tasks will be paused, but data is kept safely." if language == "en" else "⚠️ Kya aap sach me apna Telegram session disconnect karna chahte ho?",
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
            "✅ Session disconnected safely. Product data was kept." if language == "en" else "✅ Session disconnect ho gaya. Tasks aur plan safe hain.",
            reply_markup=_nav_keyboard()
        )
    await callback.answer()

@router.message(Command("connect", "login"))
async def connect_command(message: Message, state: FSMContext, db: Database, settings: Settings, telethon: TelethonService) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language): return
    if await db.has_active_session(message.from_user.id):
        await message.answer(
            "ℹ️ You're already connected. Reconnecting replaces existing session." if language == "en" else "ℹ️ Aap already connect ho. Dobara connect karne se purana replace hoga.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Reconnect anyway", callback_data="connect:force")],
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
            "ℹ️ You're already connected. Reconnecting replaces existing session." if language == "en" else "ℹ️ Aap already connect ho. Dobara connect karne se purana replace hoga.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Reconnect anyway", callback_data="connect:force")],
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
    phone = message.text.strip().replace(" ", "")
    if phone == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(safe_t(language, "login_cancelled"), reply_markup=_nav_keyboard())
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

@router.callback_query(F.data.startswith("plan:"))
async def plan_details(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    plan_name = callback.data.split(":", 1)[1]
    if plan_name not in PLANS: return await callback.answer("Invalid plan", show_alert=True)
    language = await _language_for_callback(db, callback)
    plan = PLANS[plan_name]
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans")],[InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]]) if plan_name == "free" else cycles_keyboard(plan_name)
    await callback.message.edit_text(safe_t(language, "plan_details", plan=plan.name, features=_plan_features(plan_name), monthly=plan.monthly_rupees), reply_markup=markup)
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
        [InlineKeyboardButton(text="◀️ Back", callback_data=f"plan:{plan_name}")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])
    await callback.message.edit_text(safe_t(language, "billing_details", plan=PLANS[plan_name].name, cycle=cycle.title(), original=format_paise(original), discount=format_paise(discount), payable=format_paise(payable)), reply_markup=markup)
    await callback.answer()

@router.callback_query(F.data.startswith("payment:confirm:"))
async def confirm_payment(callback: CallbackQuery, db: Database, billing: RazorpayBilling, settings: Settings) -> None:
    if callback.message is None: return
    _, _, plan_name, cycle = callback.data.split(":")
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language): return
    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(plan_name, cycle, first_paid_order=first_order)
    unique_suffix = uuid4().hex[:10]
    try:
        link = await billing.create_payment_link(amount_paise=payable, receipt=f"dk_{callback.from_user.id}_{plan_name}_{cycle}_{unique_suffix}", plan=plan_name, cycle=cycle, user_id=callback.from_user.id)
        await db.save_payment(callback.from_user.id, link.link_id, plan_name, cycle, original, discount, payable)
    except BillingError:
        return await callback.answer(safe_t(language, "payment_failed"), show_alert=True)
    
    await callback.message.edit_text(
        safe_t(language, "payment_link", plan=PLANS[plan_name].name, cycle=cycle.title(), amount=format_paise(payable)),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay Now", url=link.short_url)],
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans")],
        ])
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
    user = await db.get_user(callback.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]
    if await db.count_tasks(callback.from_user.id) >= plan.tasks:
        await callback.message.edit_text(f"⚠️ Task limit reached for {plan.name}.", reply_markup=_nav_keyboard())
        return await callback.answer()
    await state.set_state(TaskStates.waiting_name)
    await callback.message.edit_text(safe_t(language, "task_name"), reply_markup=_nav_keyboard(include_cancel=True))
    await callback.answer()

@router.message(TaskStates.waiting_name)
async def task_name(message: Message, state: FSMContext, db: Database) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    await state.update_data(task_name=message.text.strip()[:120])
    await state.set_state(TaskStates.waiting_source)
    await message.answer(safe_t(language, "task_source"), reply_markup=_nav_keyboard(include_cancel=True))

def _text_or_forwarded_chat_id(message: Message) -> str | None:
    if message.text: return message.text.strip()
    origin = getattr(message, "forward_origin", None)
    chat = getattr(origin, "chat", None) if origin is not None else None
    if chat is not None and getattr(chat, "id", None) is not None: return str(chat.id)
    legacy_chat = getattr(message, "forward_from_chat", None)
    if legacy_chat is not None and getattr(legacy_chat, "id", None) is not None: return str(legacy_chat.id)
    return None

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
    edit_task_id = data.get("edit_task_id")
    is_editing = edit_task_id is not None and data.get("edit_field") == "sources"
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        if not sources: return await message.answer("⚠️ Send at least one source." if language == "en" else "⚠️ Kam se kam ek source zaroori hai.")
        if is_editing:
            changed = await db.update_task_sources(message.from_user.id, int(edit_task_id), sources)
            await state.clear()
            if changed: await forwarding.refresh_task(int(edit_task_id))
            return await message.answer("✅ Updated." if changed else "⚠️ Error.", reply_markup=_nav_keyboard(back=f"set:task:{edit_task_id}"))
        await state.update_data(sources=sources, destinations=[])
        await state.set_state(TaskStates.waiting_destination)
        return await message.answer(safe_t(language, "task_destination"), reply_markup=_nav_keyboard(include_cancel=True))

    if len(sources) >= plan.sources_per_task:
        return await message.answer(f"⚠️ Max sources reached ({plan.sources_per_task}). Send /done.")
    try:
        entity = await telethon.validate_for_user(message.from_user.id, text)
    except ValueError as exc:
        return await message.answer(f"⚠️ {safe_html(exc)}")
    sources.append(entity)
    await state.update_data(sources=sources)
    remaining = plan.sources_per_task - len(sources)
    if remaining > 0:
        await message.answer(f"✅ Source added. Send another, or /done ({remaining} left).")
    else:
        if is_editing:
            changed = await db.update_task_sources(message.from_user.id, int(edit_task_id), sources)
            await state.clear()
            if changed: await forwarding.refresh_task(int(edit_task_id))
            return await message.answer("✅ Updated." if changed else "⚠️ Error.", reply_markup=_nav_keyboard(back=f"set:task:{edit_task_id}"))
        await state.update_data(destinations=[])
        await state.set_state(TaskStates.waiting_destination)
        await message.answer(safe_t(language, "task_destination"), reply_markup=_nav_keyboard(include_cancel=True))

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
    edit_task_id = data.get("edit_task_id")
    is_editing = edit_task_id is not None and data.get("edit_field") == "destinations"
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        if not destinations: return await message.answer("⚠️ Send at least one destination." if language == "en" else "⚠️ Kam se kam ek destination zaroori hai.")
    elif len(destinations) >= plan.destinations_per_task:
        return await message.answer(f"⚠️ Max destinations reached ({plan.destinations_per_task}). Send /done.")
    else:
        try:
            destination = await telethon.validate_for_user(message.from_user.id, text)
        except ValueError as exc:
            return await message.answer(f"⚠️ {safe_html(exc)}")
        destinations.append(destination)
        await state.update_data(destinations=destinations)
        remaining = plan.destinations_per_task - len(destinations)
        if remaining > 0:
            return await message.answer(f"✅ Destination added. Send another, or /done ({remaining} left).")

    if is_editing:
        changed = await db.update_task_destinations(message.from_user.id, int(edit_task_id), destinations)
        await state.clear()
        if changed: await forwarding.refresh_task(int(edit_task_id))
        return await message.answer("✅ Updated." if changed else "⚠️ Error.", reply_markup=_nav_keyboard(back=f"set:task:{edit_task_id}"))

    data = await state.get_data()
    task_id = await db.create_task_multi(message.from_user.id, str(data["task_name"]), list(data["sources"]), list(data["destinations"]))
    await state.clear()
    await forwarding.refresh_task(task_id)
    await _notify_admins(message.bot, settings, f"➕ New task\nID: {task_id}")
    await message.answer(safe_t(language, "task_created", task_id=task_id), reply_markup=_nav_keyboard())

@router.message(Command("pause", "resume", "deletetask"))
async def task_action(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split()
    cmd = parts[0].lstrip("/").lower() if parts else ""
    if len(parts) != 2 or not parts[1].isdigit():
        return await message.answer(f"Usage: /{cmd} &lt;task_id&gt;\nOr use /tasks buttons.")
    task_id = int(parts[1])
    if cmd == "deletetask":
        changed = await db.delete_task(message.from_user.id, task_id)
        if changed: await forwarding.remove_task(task_id)
        await message.answer("🗑️ Deleted." if changed else "⚠️ Not found.")
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
async def task_delete_prompt(callback: CallbackQuery) -> None:
    if callback.message is None: return
    task_id = int(callback.data.split(":")[2])
    await callback.message.edit_text(f"⚠️ Delete task #{task_id} permanently?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Delete", callback_data=f"task:delete-confirm:{task_id}")],
        [InlineKeyboardButton(text="✖️ Cancel", callback_data="menu:tasks")]
    ]))
    await callback.answer()

@router.callback_query(F.data.startswith("task:delete-confirm:"))
async def task_delete_confirm_cb(callback: CallbackQuery, db: Database, forwarding: ForwardingEngine) -> None:
    task_id = int(callback.data.split(":")[2])
    changed = await db.delete_task(callback.from_user.id, task_id)
    if changed: await forwarding.remove_task(task_id)
    if callback.message: await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer("Deleted" if changed else "Not found")

# ==========================================
# DYNAMIC PLAN SETTINGS (/setting)
# ==========================================

TIER_FEATURES: dict[str, set[str]] = {
    "free": set(),
    "silver": {"header", "footer"},
    "gold": {"header", "footer", "blacklist", "whitelist", "replace"},
    "platinum": {"header", "footer", "blacklist", "whitelist", "replace", "watermark", "auto_delete_seconds", "edit_sync", "user_filter"},
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
    
    rows = [
        [InlineKeyboardButton(text="💬 Message Settings", callback_data=f"set:cat:{task_id}:msg")],
        [InlineKeyboardButton(text="🔍 Filters", callback_data=f"set:cat:{task_id}:flt"), InlineKeyboardButton(text="🖼️ Media", callback_data=f"set:cat:{task_id}:med")],
        [InlineKeyboardButton(text="🚀 Forwarding", callback_data=f"set:cat:{task_id}:fwd"), InlineKeyboardButton(text="👤 Sender Filter", callback_data=f"set:cat:{task_id}:snd")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="menu:settings"), InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]
    ]
    await callback.message.edit_text(f"⚙️ <b>Settings for:</b> {safe_html(task['task_name'])}\nChoose a category:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
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

@router.callback_query(F.data.startswith("set:cat:"))
async def setting_category(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None: return
    _, _, task_id_str, cat = callback.data.split(":")
    task_id = int(task_id_str)
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != callback.from_user.id: return await callback.answer("Not found", show_alert=True)
    user = await db.get_user(callback.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    import json
    st = task["settings"] if isinstance(task["settings"], dict) else json.loads(task["settings"] or "{}")

    rows = []
    text = ""
    if cat == "msg":
        text = "💬 <b>Message Settings</b>"
        rows.append([_get_setting_btn("Header", "header", task_id, plan_name)])
        rows.append([_get_setting_btn("Footer", "footer", task_id, plan_name)])
        rows.append([_get_setting_btn("Text Replace", "replace", task_id, plan_name)])
    elif cat == "flt":
        text = "🔍 <b>Filters</b>"
        rows.append([_get_setting_btn("Blacklist", "blacklist", task_id, plan_name)])
        rows.append([_get_setting_btn("Whitelist", "whitelist", task_id, plan_name)])
    elif cat == "med":
        text = "🖼️ <b>Media Settings</b>"
        rows.append([_get_toggle_btn("Watermark", "watermark", task_id, plan_name, st)])
        rows.append([_get_setting_btn("Auto Delete", "auto_delete_seconds", task_id, plan_name)])
    elif cat == "fwd":
        text = "🚀 <b>Forwarding Settings</b>"
        rows.append([_get_toggle_btn("Live Edit Sync", "edit_sync", task_id, plan_name, st)])
        rows.append([InlineKeyboardButton(text="📥 Sources", callback_data=f"task:edit-source:{task_id}"), InlineKeyboardButton(text="📤 Destinations", callback_data=f"task:edit-dest:{task_id}")])
    elif cat == "snd":
        text = "👤 <b>Sender Filter</b>"
        rows.append([_get_setting_btn("Sender Filter", "user_filter", task_id, plan_name)])
    
    rows.append([InlineKeyboardButton(text="◀️ Back", callback_data=f"set:task:{task_id}"), InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("set:lock:"))
async def setting_locked(callback: CallbackQuery) -> None:
    if callback.message is None: return
    await callback.message.edit_text(
        "🔒 <b>Feature Locked</b>\n\nThis feature is not available on your current plan. Please upgrade to unlock it.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:settings")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("set:tog:"))
async def setting_toggle(callback: CallbackQuery, db: Database) -> None:
    _, _, task_id_str, feature, val_str = callback.data.split(":")
    task_id = int(task_id_str)
    user = await db.get_user(callback.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    if feature not in TIER_FEATURES.get(plan_name, set()): return await callback.answer("Locked feature", show_alert=True)
    val = val_str == "on"
    await db.update_task_settings(callback.from_user.id, task_id, {feature: val})
    cat = "med" if feature == "watermark" else "fwd"
    if callback.message:
        callback.data = f"set:cat:{task_id}:{cat}"
        await setting_category(callback, db)
    await callback.answer("✅ Updated")

@router.callback_query(F.data.startswith("set:edit:"))
async def setting_edit_input(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.message is None: return
    _, _, task_id_str, feature = callback.data.split(":")
    task_id = int(task_id_str)
    
    user = await db.get_user(callback.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    if feature not in TIER_FEATURES.get(plan_name, set()): return await callback.answer("Locked", show_alert=True)
    
    cat = "msg" if feature in ("header", "footer", "replace") else "flt" if feature in ("blacklist", "whitelist") else "med" if feature == "auto_delete_seconds" else "snd"
    
    await state.set_state(SettingsFlow.waiting_value)
    await state.update_data(task_id=task_id, feature=feature, cat=cat)
    
    prompts = {
        "header": "Enter new Header text. Type /clear to remove.",
        "footer": "Enter new Footer text. Type /clear to remove.",
        "replace": "Enter replace rules (e.g. `old=>new, apple=>orange`). Type /clear to remove.",
        "blacklist": "Enter comma separated blacklist words. Type /clear to remove.",
        "whitelist": "Enter comma separated whitelist words. Type /clear to remove.",
        "auto_delete_seconds": "Enter seconds to auto-delete (e.g. 3600). Type /clear to turn off.",
        "user_filter": "Enter comma separated Telegram Sender IDs. Type /clear to remove."
    }
    await callback.message.edit_text(f"✏️ <b>{feature.title()}</b>\n\n{prompts.get(feature, 'Enter value:')}", reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"), parse_mode="HTML")
    await callback.answer()

@router.message(SettingsFlow.waiting_value)
async def setting_save_value(message: Message, state: FSMContext, db: Database, forwarding: ForwardingEngine) -> None:
    if not message.text: return
    data = await state.get_data()
    task_id = data["task_id"]
    feature = data["feature"]
    cat = data["cat"]
    val = message.text.strip()
    
    clear = val.lower() == "/clear"
    
    update_val = None
    if feature in ("header", "footer"):
        update_val = "" if clear else val
    elif feature in ("blacklist", "whitelist"):
        update_val = [] if clear else [w.strip() for w in val.split(",") if w.strip()]
    elif feature == "user_filter":
        if clear: update_val = []
        else:
            try: update_val = [int(w.strip()) for w in val.split(",") if w.strip()]
            except ValueError: return await message.answer("⚠️ Must be valid numeric IDs.", reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"))
    elif feature == "auto_delete_seconds":
        if clear: update_val = 0
        else:
            try: update_val = int(val)
            except ValueError: return await message.answer("⚠️ Must be a number in seconds.", reply_markup=_nav_keyboard(back=f"set:cat:{task_id}:{cat}"))
    elif feature == "replace":
        if clear: update_val = {}
        else:
            mapping = {}
            for pair in val.split(","):
                if "=>" in pair:
                    o, n = pair.split("=>", 1)
                    mapping[o.strip()] = n.strip()
            update_val = mapping
            
    await db.update_task_settings(message.from_user.id, task_id, {feature: update_val})
    await forwarding.refresh_task(task_id)
    await state.clear()
    await message.answer("✅ Setting saved.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Back to Category", callback_data=f"set:cat:{task_id}:{cat}")]]))

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
            [InlineKeyboardButton(text="Hinglish", callback_data="admin:broadcast:hinglish"), InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")]
        ])
    )

@router.callback_query(F.data.startswith("admin:broadcast:"))
async def broadcast_send(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, callback.from_user.id): return await callback.answer("Admin only", show_alert=True)
    audience = callback.data.rsplit(":", 1)[1]
    if audience == "start":
        await state.set_state(AdminBroadcastStates.waiting_message)
        await callback.message.edit_text("📣 Send broadcast message. /back to cancel.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Cancel", callback_data="admin:home")]]))
        return await callback.answer()
    data = await state.get_data()
    text = str(data.get("broadcast_text", "")).strip()
    if not text: return await callback.answer("Missing text", show_alert=True)
    users = await db.list_broadcast_users(audience)
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

@router.message(Command("block", "unblock"))
async def block_user_command(message: Message, db: Database, settings: Settings, forwarding: ForwardingEngine) -> None:
    if not _is_admin(settings, message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 2: return await message.answer("Usage: /block &lt;telegram_user_id or @username&gt;")
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
    if len(parts) not in (3, 4) or not parts[2].isdigit(): return await message.answer("Usage: /grantdays &lt;user&gt; &lt;days&gt; [plan]")
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

@router.message(Command("setplan"))
async def set_plan_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    parts = (message.text or "").split()
    if len(parts) != 4 or parts[2].lower() not in PLANS or not parts[3].isdigit(): return await message.answer("Usage: /setplan &lt;user&gt; &lt;plan&gt; &lt;days&gt;")
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None: return await message.answer("⚠️ Not found.")
    changed = await db.set_plan(user_id, parts[2].lower(), int(parts[3]))
    await message.answer("✅ Updated." if changed else "⚠️ Not found.")

@router.message(Command("listusers"))
async def list_users_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id): return
    users = await db.list_users(15)
    if not users: return await message.answer("No users found.")
    for u in users:
        label = safe_html(u["first_name"] or u["username"] or "No name")
        block_label = "✅ Unblock" if u["is_blocked"] else "⛔ Block"
        block_action = "unblock" if u["is_blocked"] else "block"
        await message.answer(
            f"👤 {label}\nID: {u['telegram_user_id']}\nPlan: {u['plan']}\nBlocked: {'Yes' if u['is_blocked'] else 'No'}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="🎁 Grant Days", callback_data=f"admin:grant:{u['telegram_user_id']}"),
                InlineKeyboardButton(text=block_label, callback_data=f"admin:{block_action}:{u['telegram_user_id']}")
            ]]), parse_mode="HTML"
        )

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
    if len(parts) != 2: return await message.answer("Usage: /userinfo &lt;user&gt;")
    user_id = await _resolve_target_user(db, parts[1])
    user = await db.get_user(user_id) if user_id else None
    if not user: return await message.answer("⚠️ Not found.")
    await message.answer(f"👤 {user['telegram_user_id']}\nPlan: {user['plan']}\nExpiry: {user['plan_expiry']}\nBlocked: {user['is_blocked']}")

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

def build_app(bot: Bot, db: Database, settings: Settings, billing: RazorpayBilling) -> FastAPI:
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
        try:
            payload = billing.parse_json(raw_body)
            captured = billing.parse_captured_payment(payload)
            if captured is None: return JSONResponse({"status": "ignored"})
            
            stored_payment = await db.get_payment_for_order(captured.order_id)
            if stored_payment is None:
                return JSONResponse({"status": "ignored"})
                
            stored_plan = str(stored_payment["plan"])
            stored_cycle = str(stored_payment["cycle"])
            
            user_id = await db.activate_payment(
                captured.order_id, captured.payment_id, captured.amount_paise,
                duration_days(stored_cycle), stored_plan, stored_cycle
            )
            
        except (BillingError, ValueError) as exc:
            logger.warning("Rejected webhook: %s", str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
            
        if user_id is not None:
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
    await bot.set_my_commands(_bot_commands())
    for admin_id in settings.admin_telegram_ids:
        await bot.set_my_commands(_bot_commands() + _admin_bot_commands(), scope=BotCommandScopeChat(chat_id=admin_id))

    api = build_app(bot, db, settings, billing)
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
        # FIX: Run the forwarding engine startup in a background task so it doesn't block Uvicorn healthchecks
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
