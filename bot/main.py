# main.py
from __future__ import annotations
import asyncio
import html
import logging
import os
import json
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
    ErrorEvent, BotCommand, BotCommandScopeChat, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
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
from .locales import ADMIN_COMMANDS, USER_COMMANDS, PREMIUM_COMMANDS, admin_help, command_help, language_for, t
from .plans import PLANS, duration_days, format_paise, payable_amount_paise
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti")
router = Router(name="dealskoti")

# ==========================================
# FSM STATES (Added File Upload)
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
    
class UploadStates(StatesGroup):
    waiting_for_file = State()

# ... [Keep all helper functions _is_admin, _ensure_user, _language_for_message etc. exactly the same] ...
def safe_html(text: str) -> str: return html.escape(str(text))
def safe_t(lang: str, key: str, **kwargs) -> str:
    try: return t(lang, key, **kwargs)
    except KeyError: return f"[{key}]"
async def _language_for_message(db: Database, message: Message):
    user = await _ensure_user(db, message)
    return language_for(user["preferred_language"])
async def _ensure_user(db: Database, message: Message):
    if message.from_user is None: raise RuntimeError("Telegram user is missing")
    return await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

# ==========================================
# UI KEYBOARDS (Added 1-20 Chat Picker)
# ==========================================
def chat_picker_keyboard() -> InlineKeyboardMarkup:
    """1-20 Inline Button UI inspired by competitor bot"""
    rows = []
    # 4 rows of 5 buttons (1 to 20)
    for i in range(1, 21, 5):
        row = [InlineKeyboardButton(text=str(j), callback_data=f"pick_chat:{j}") for j in range(i, i+5)]
        rows.append(row)
    rows.append([InlineKeyboardButton(text="◀️ Back / Done", callback_data="chat_picker:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _nav_keyboard(*, back: str = "menu:home", include_cancel: bool = False) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="◀️ Back", callback_data=back)]]
    if include_cancel: rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ==========================================
# NEW PREMIUM COMMANDS HANDLER
# ==========================================
@router.message(Command(*PREMIUM_COMMANDS))
async def premium_commands_handler(message: Message, db: Database) -> None:
    """Catches all new premium commands from /hide_head to /convert_amazon_links"""
    user = await db.get_user(message.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    plan_name = str(user["plan"]) if user else "free"
    
    cmd_used = message.text.split()[0].lstrip("/")
    
    if plan_name not in ["gold", "platinum"]:
        # Block access and show the "Upgrade Required" message
        await message.answer(
            safe_t(language, "feature_locked", feature=f"/{cmd_used}"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")]
            ]),
            parse_mode="HTML"
        )
    else:
        # Allow access: Guide them to the interactive setting menu since features are per-task
        await message.answer(
            f"✅ **{cmd_used.replace('_', ' ').title()}** is unlocked for Premium users.\n\nTo configure this, please go to your Task Settings by clicking below:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Go to Task Settings", callback_data="menu:settings")]
            ])
        )

# ==========================================
# NEW CONFIGURATION OVERVIEW (/config)
# ==========================================
@router.message(Command("config"))
async def config_command(message: Message, db: Database) -> None:
    user = await db.get_user(message.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    tasks = await db.list_tasks(message.from_user.id)
    
    if not tasks:
        await message.answer("⛔ No Configuration found. Create a task first with /newtask.")
        return
        
    # Showing config for the first task as an example representation
    task = tasks[0]
    st = json.loads(task["settings"] or "{}")
    srcs = json.loads(task["sources"] or "[]")
    dsts = json.loads(task["destinations"] or "[]")
    
    def tf(val): return "⚡ ON" if val else "⚡ OFF [Disabled]"
    def text_val(val): return val if val else "⛔ [Empty]"
    
    formatted_config = safe_t(
        language, "config_ascii",
        sources=f"{len(srcs)} Channels Configured" if srcs else "⛔ No Source Channels Configured.",
        destinations=f"{len(dsts)} Targets Configured" if dsts else "⛔ No Target Channels Configured.",
        fwd_status=tf(not task["is_paused"]),
        header_status=tf(bool(st.get("header"))),
        media_status=tf(True), # Assuming media is native
        url_preview=tf(st.get("url_preview")),
        remove_links=tf(st.get("remove_links")),
        remove_usernames=tf(st.get("remove_usernames")),
        repeat_post=tf(st.get("repeat_post")),
        auto_delete=tf(st.get("auto_delete_seconds", 0) > 0),
        link_replies=tf(st.get("reply_sync")),
        post_edit=tf(st.get("edit_sync")),
        amazon_conv=tf(st.get("amazon_converter")),
        disable_links=tf(st.get("disable_links")),
        mono_text=tf(st.get("mono_text")),
        protected_fwd=tf(st.get("protected")),
        auto_reaction=tf(st.get("auto_reaction")),
        blacklist=text_val(", ".join(st.get("blacklist", []))),
        whitelist=text_val(", ".join(st.get("whitelist", []))),
        trim=text_val(", ".join(st.get("trim_words", []))),
        replace_links=text_val(str(st.get("replace_links", ""))),
        replace_users=text_val(str(st.get("replace_users", ""))),
        replace_words=text_val(str(st.get("replace", ""))),
        header_text=text_val(st.get("header")),
        footer_text=text_val(st.get("footer")),
        delay_timer=f"[ {st.get('delay_seconds', 0)} Seconds]"
    )
    
    await message.answer(f"<code>{formatted_config}</code>", parse_mode="HTML")

# ==========================================
# NEW FILE UPLOAD SYSTEM (/upload)
# ==========================================
@router.message(Command("upload", "uploadfile"))
async def upload_file_command(message: Message, state: FSMContext, db: Database) -> None:
    user = await db.get_user(message.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    
    await state.set_state(UploadStates.waiting_for_file)
    await message.answer(
        safe_t(language, "upload_prompt"), 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]])
    )

@router.message(UploadStates.waiting_for_file, F.document)
async def handle_file_upload(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    # Requires a DUMMY_CHANNEL_ID in your Settings (e.g. settings.dummy_channel_id)
    # The bot will forward the file to the dummy channel, grab the message ID, and save to DB
    dummy_chat_id = os.getenv("DUMMY_CHANNEL_ID", settings.update_channel_username) # fallback if not set
    
    try:
        # Forward file to dummy storage channel
        sent_msg = await message.bot.send_document(chat_id=dummy_chat_id, document=message.document.file_id)
        
        # Save reference to DB
        file_name = message.document.file_name or "uploaded_file"
        await db.save_user_file(message.from_user.id, message.document.file_id, file_name, sent_msg.message_id)
        
        language = await _language_for_message(db, message)
        await message.answer(
            safe_t(language, "upload_success", file_id=message.document.file_id),
            reply_markup=_nav_keyboard()
        )
    except Exception as e:
        await message.answer(f"⚠️ Failed to upload file to storage: {e}")
    finally:
        await state.clear()

# ==========================================
# UPDATED PLANS COMMAND (ASCII & USDT)
# ==========================================
@router.message(Command("plans", "subscribe"))
async def plans_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    user = await db.get_user(message.from_user.id)
    
    # Send Basic Plan ASCII
    await message.answer(f"<code>{safe_t(language, 'basic_ascii')}</code>", parse_mode="HTML", 
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="💳 Pay Basic (INR/Razorpay)", callback_data="plan:silver")],
                             [InlineKeyboardButton(text="🪙 Pay Basic (USDT)", callback_data="pay:usdt:basic")]
                         ]))
    
    # Send Premium Plan ASCII
    await message.answer(f"<code>{safe_t(language, 'premium_ascii')}</code>", parse_mode="HTML",
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="💳 Pay Premium (INR/Razorpay)", callback_data="plan:platinum")],
                             [InlineKeyboardButton(text="🪙 Pay Premium (USDT)", callback_data="pay:usdt:premium")]
                         ]))

@router.callback_query(F.data.startswith("pay:usdt:"))
async def usdt_payment_callback(callback: CallbackQuery, db: Database) -> None:
    language = await _language_for_callback(db, callback)
    await callback.message.edit_text(
        safe_t(language, "manual_usdt_pay"),
        parse_mode="Markdown",
        reply_markup=_nav_keyboard()
    )
    await callback.answer()

# ==========================================
# UPDATED TASK CREATION (1-20 PICKER)
# ==========================================
# In your existing TaskStates.waiting_source handler, you would inject the picker
@router.message(TaskStates.waiting_name)
async def task_name(message: Message, state: FSMContext, db: Database) -> None:
    if not message.text: return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        return await message.answer("↩️ Cancelled.", reply_markup=_nav_keyboard())
    
    await state.update_data(task_name=message.text.strip()[:120])
    await state.set_state(TaskStates.waiting_source)
    
    # Send Picker Keyboard here!
    await message.answer(
        safe_t(language, "task_source") + "\n\n(Top 20 Chat IDs shown below)", 
        reply_markup=chat_picker_keyboard()
    )

# ... [KEEP ALL EXISTING RAZORPAY, FASTAPI, AND TELETHON FORWARDING LOGIC UNCHANGED HERE] ...
