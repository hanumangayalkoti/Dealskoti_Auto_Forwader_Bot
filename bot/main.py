from __future__ import annotations

import asyncio
import html
import logging
import re
from contextlib import suppress
from datetime import datetime, timezone

import uvicorn
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand, BotCommandScopeChat, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .billing import BillingError, RazorpayBilling
from .config import ConfigurationError, Settings
from .db import Database
from .faq import FAQS
from .forwarding import ForwardingEngine
from .gate import enforce_gate
from .locales import ADMIN_COMMANDS, USER_COMMANDS, command_help, language_for, t
from .plans import PLANS, format_paise, payable_amount_paise
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti")
router = Router(name="dealskoti")


class LoginStates(StatesGroup):
    phone = State()
    otp = State()
    two_fa = State()


class TaskStates(StatesGroup):
    name = State()
    source = State()
    destination = State()


class SettingsStates(StatesGroup):
    value = State()


class UploadStates(StatesGroup):
    file = State()


class AdminStates(StatesGroup):
    broadcast = State()

def h(value: object) -> str:
    return html.escape(str(value))


async def ensure_user(db: Database, message: Message):
    user = message.from_user
    if not user:
        raise RuntimeError("Telegram user is missing")
    return await db.ensure_user(user.id, user.username, user.first_name)


async def user_language(db: Database, message: Message) -> str:
    user = await ensure_user(db, message)
    return language_for(user["preferred_language"])


async def guard(message: Message, db: Database, settings: Settings) -> bool:
    user = await ensure_user(db, message)
    if user["is_blocked"]:
        return False
    return await enforce_gate(message.bot, db, settings, user["telegram_user_id"], language_for(user["preferred_language"]))


def main_keyboard(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "en": [
            ("Connect Account", "menu:connect"), ("My Accounts", "menu:account"),
            ("New Task", "menu:newtask"), ("My Tasks", "menu:tasks"),
            ("Plans", "menu:plans"), ("Settings", "menu:settings"),
            ("FAQ", "menu:faq:0"), ("Language", "menu:language"),
            ("Support", "menu:support"), ("Refer", "menu:refer"),
        ],
        "hinglish": [
            ("Connect Account", "menu:connect"), ("My Accounts", "menu:account"),
            ("New Task", "menu:newtask"), ("My Tasks", "menu:tasks"),
            ("Plans", "menu:plans"), ("Settings", "menu:settings"),
            ("FAQ", "menu:faq:0"), ("Language", "menu:language"),
            ("Support", "menu:support"), ("Refer", "menu:refer"),
        ],
    }[lang]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=labels[0][0], callback_data=labels[0][1]),
         InlineKeyboardButton(text=labels[1][0], callback_data=labels[1][1])],
        [InlineKeyboardButton(text=labels[2][0], callback_data=labels[2][1]),
         InlineKeyboardButton(text=labels[3][0], callback_data=labels[3][1])],
        [InlineKeyboardButton(text=labels[4][0], callback_data=labels[4][1]),
         InlineKeyboardButton(text=labels[5][0], callback_data=labels[5][1])],
        [InlineKeyboardButton(text=labels[6][0], callback_data=labels[6][1]),
         InlineKeyboardButton(text=labels[7][0], callback_data=labels[7][1])],
        [InlineKeyboardButton(text=labels[8][0], callback_data=labels[8][1]),
         InlineKeyboardButton(text=labels[9][0], callback_data=labels[9][1])],
    ])


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Home", callback_data="menu:home")]
    ])


def back_keyboard(callback_data: str = "menu:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Back", callback_data=callback_data)],
        [InlineKeyboardButton(text="Home", callback_data="menu:home")],
    ])


def plan_features(name: str) -> str:
    plan = PLANS[name]
    daily = "Unlimited" if plan.daily_messages is None else str(plan.daily_messages)
    extra = {
        "free": ["Forwarded tag", "Header and Footer"],
        "silver": ["A to B forwarding", "Media forwarding", "Remove usernames/links", "Delay timer", "Anti-ban speed"],
        "gold": ["Everything in Silver", "Blacklist and whitelist", "Disable hidden links", "Anti-ban speed"],
        "platinum": ["Everything in Gold", "Link preview", "Auto delete", "Replacements", "Image watermark", "File attachment", "VIP support"],
    }[name]
    return "\n".join([
        f"Tasks: {plan.tasks}",
        f"Sources: {plan.sources_per_task} | Destinations: {plan.destinations_per_task}",
        f"Messages/day: {daily}",
        *[f"• {item}" for item in extra],
    ])


def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Free", callback_data="plan:free"),
         InlineKeyboardButton(text="Silver", callback_data="plan:silver")],
        [InlineKeyboardButton(text="Gold", callback_data="plan:gold"),
         InlineKeyboardButton(text="Platinum", callback_data="plan:platinum")],
        [InlineKeyboardButton(text="Home", callback_data="menu:home")],
    ])


async def edit_or_answer(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    if callback.message:
        with suppress(Exception):
            await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


async def show_home(message: Message, db: Database) -> None:
    user = await ensure_user(db, message)
    lang = language_for(user["preferred_language"])
    name = user["first_name"] or user["username"] or "User"
    await message.answer(t(lang, "main_menu", name=h(name)), reply_markup=main_keyboard(lang))


@router.message(CommandStart())
async def start(message: Message, db: Database) -> None:
    await ensure_user(db, message)
    await show_home(message, db)


@router.message(Command("menu"))
async def menu_command(message: Message, db: Database) -> None:
    await show_home(message, db)


@router.message(Command("help"))
async def help_command(message: Message, db: Database, settings: Settings) -> None:
    if not await guard(message, db, settings):
        return
    lang = await user_language(db, message)
    await message.answer("Help\n\n" + command_help(lang), reply_markup=back_keyboard())


@router.message(Command("updates"))
async def updates_command(message: Message, settings: Settings) -> None:
    """Give users a direct way to open the required updates channel."""
    channel = settings.update_channel_username
    await message.answer(
        f"Updates channel: https://t.me/{channel.lstrip('@')}",
        reply_markup=home_keyboard(),
    )


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, db: Database) -> None:
    if callback.message:
        user = await ensure_user(db, callback.message)
        lang = language_for(user["preferred_language"])
        name = user["first_name"] or user["username"] or "User"
        await edit_or_answer(callback, t(lang, "main_menu", name=h(name)), main_keyboard(lang))
    else:
        await callback.answer()


@router.callback_query(F.data == "menu:connect")
async def connect_button(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    lang = await user_language(db, callback.message)
    await state.set_state(LoginStates.phone)
    await edit_or_answer(callback, t(lang, "connect_phone"), back_keyboard())


@router.message(Command("connect"))
async def connect_command(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await guard(message, db, settings):
        return
    lang = await user_language(db, message)
    await state.set_state(LoginStates.phone)
    await message.answer(t(lang, "connect_phone"), reply_markup=back_keyboard())


@router.message(LoginStates.phone)
async def receive_phone(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    value = (message.text or "").strip()
    lang = await user_language(db, message)
    if not re.fullmatch(r"\+\d{7,15}", value):
        await message.answer(t(lang, "connect_phone") + "\n\nPlease send a valid number.")
        return
    try:
        await telethon.start_phone_login(message.from_user.id, value)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.set_state(LoginStates.otp)
    await message.answer(t(lang, "connect_otp"))


@router.message(LoginStates.otp)
async def receive_otp(message: Message, state: FSMContext, telethon: TelethonService, db: Database, engine: ForwardingEngine) -> None:
    lang = await user_language(db, message)
    try:
        result = await telethon.submit_pin(message.from_user.id, (message.text or "").strip())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    if result == "2fa_required":
        await state.set_state(LoginStates.two_fa)
        await message.answer(t(lang, "connect_2fa"))
        return
    await state.clear()
    await engine.refresh_user(message.from_user.id)
    await message.answer(t(lang, "connected"), reply_markup=home_keyboard())


@router.message(LoginStates.two_fa)
async def receive_2fa(message: Message, state: FSMContext, telethon: TelethonService, db: Database, engine: ForwardingEngine) -> None:
    lang = await user_language(db, message)
    try:
        await telethon.submit_2fa(message.from_user.id, (message.text or "").strip())
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await engine.refresh_user(message.from_user.id)
    await message.answer(t(lang, "connected"), reply_markup=home_keyboard())


@router.callback_query(F.data == "menu:account")
@router.message(Command("account"))
async def account_view(event: CallbackQuery | Message, db: Database, settings: Settings) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if not await guard(message, db, settings):
        return
    user = await ensure_user(db, message)
    lang = language_for(user["preferred_language"])
    payment = await db.get_last_payment(user["telegram_user_id"])
    tasks = await db.list_tasks(user["telegram_user_id"])
    started = user["plan_started_at"].strftime("%d %b %Y") if user["plan_started_at"] else "—"
    expiry = user["plan_expiry"].strftime("%d %b %Y") if user["plan_expiry"] else "—"
    txn = payment["payment_id"] if payment and payment["payment_id"] else "—"
    text = t(lang, "account", name=h(user["first_name"] or "User"),
             username=h("@" + user["username"] if user["username"] else "—"),
             user_id=user["telegram_user_id"], plan=h(str(user["plan"]).title()),
             started=started, expiry=expiry, txn=h(txn),
             tasks=len(tasks), usage=await db.get_usage_today(user["telegram_user_id"]),
             session="Connected" if await db.has_active_session(user["telegram_user_id"]) else "Not connected")
    if isinstance(event, CallbackQuery):
        await edit_or_answer(event, text, back_keyboard())
    else:
        await message.answer(text, reply_markup=back_keyboard())


@router.message(Command("disconnect"))
async def disconnect_command(message: Message, db: Database, telethon: TelethonService, engine: ForwardingEngine, settings: Settings) -> None:
    if not await guard(message, db, settings):
        return
    await engine.remove_user(message.from_user.id)
    await telethon.disconnect(message.from_user.id)
    await message.answer("Telegram account disconnected.", reply_markup=home_keyboard())


@router.callback_query(F.data == "menu:plans")
@router.message(Command("plans"))
async def plans_view(event: CallbackQuery | Message, db: Database, settings: Settings) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if not await guard(message, db, settings):
        return
    text = "Plans and features\n\n" + "\n\n".join(
        f"{name.title()} — ₹{plan.monthly_rupees} / ${plan.usdt_price}\n{plan_features(name)}"
        for name, plan in PLANS.items()
    )
    if isinstance(event, CallbackQuery):
        await edit_or_answer(event, text, plans_keyboard())
    else:
        await message.answer(text, reply_markup=plans_keyboard())


@router.callback_query(F.data.startswith("plan:"))
async def plan_detail(callback: CallbackQuery, db: Database) -> None:
    plan_name = callback.data.split(":", 1)[1]
    if plan_name not in PLANS:
        await callback.answer("Unknown plan", show_alert=True)
        return
    plan = PLANS[plan_name]
    text = f"{plan.name}\n\n{plan_features(plan_name)}\n\nINR: ₹{plan.monthly_rupees}\nUSDT: ${plan.usdt_price}"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Weekly INR", callback_data=f"cycle:{plan_name}:weekly:inr"),
         InlineKeyboardButton(text="Monthly INR", callback_data=f"cycle:{plan_name}:monthly:inr")],
        [InlineKeyboardButton(text="Yearly INR", callback_data=f"cycle:{plan_name}:yearly:inr")],
        [InlineKeyboardButton(text="Weekly USDT", callback_data=f"usdt:{plan_name}:weekly"),
         InlineKeyboardButton(text="Monthly USDT", callback_data=f"usdt:{plan_name}:monthly")],
        [InlineKeyboardButton(text="Yearly USDT", callback_data=f"usdt:{plan_name}:yearly")],
        [InlineKeyboardButton(text="Back", callback_data="menu:plans")],
    ])
    await edit_or_answer(callback, text, markup)


@router.callback_query(F.data.startswith("usdt:"))
async def usdt_payment(callback: CallbackQuery) -> None:
    await edit_or_answer(callback, "USDT payment is manual.\n\nSend the payment proof and transaction hash to support. The admin will verify it and activate the plan.")


@router.callback_query(F.data.startswith("cycle:"))
async def razorpay_cycle(callback: CallbackQuery, db: Database, settings: Settings, billing: RazorpayBilling) -> None:
    _, plan_name, cycle, _ = callback.data.split(":")
    user = await ensure_user(db, callback.message)
    original, discount, payable = payable_amount_paise(plan_name, cycle)
    if payable <= 0:
        await callback.answer("Free plan does not require payment.", show_alert=True)
        return
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        await edit_or_answer(callback, "Razorpay is not configured. Please contact support.")
        return
    # Razorpay limits reference_id to 40 characters.
    order_id = f"dk_{user['telegram_user_id']}_{int(datetime.now().timestamp())}"
    await db.create_payment(user["telegram_user_id"], order_id, plan_name, cycle, original, discount, payable)
    try:
        result = await billing.create_payment_link(payable, order_id, plan_name, cycle, user["telegram_user_id"])
    except BillingError:
        await edit_or_answer(callback, "Payment link could not be created. Please try again later.")
        return
    await edit_or_answer(callback, f"Payment link ready.\n\nPlan: {plan_name.title()}\nCycle: {cycle}\nAmount: {format_paise(payable)}",
                         InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="Pay with Razorpay", url=result.short_url)],
                             [InlineKeyboardButton(text="Home", callback_data="menu:home")],
                         ]))


def chat_picker_keyboard(chats: list[dict], kind: str, selected: list[dict], limit: int) -> InlineKeyboardMarkup:
    rows = []
    selected_ids = {str(item["id"]) for item in selected}
    for index, chat in enumerate(chats[:20]):
        mark = "✓ " if str(chat["id"]) in selected_ids else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{chat['title'][:28]}", callback_data=f"chat:{kind}:{index}")])
    rows.append([InlineKeyboardButton(text=f"Done ({len(selected)}/{limit})", callback_data=f"chat_done:{kind}")])
    rows.append([InlineKeyboardButton(text="Manual chat", callback_data=f"chat_manual:{kind}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_picker(message: Message, state: FSMContext, telethon: TelethonService, kind: str, db: Database) -> None:
    data = await state.get_data()
    user = await ensure_user(db, message)
    plan = PLANS.get(user["plan"], PLANS["free"])
    chats = await telethon.recent_chats(user["telegram_user_id"])
    await state.update_data(chat_options=chats, picker_kind=kind)
    selected = data.get(kind, [])
    limit = plan.sources_per_task if kind == "source" else plan.destinations_per_task
    lang = language_for(user["preferred_language"])
    await message.answer(t(lang, "task_source" if kind == "source" else "task_destination"),
                         reply_markup=chat_picker_keyboard(chats, kind, selected, limit))


@router.callback_query(F.data == "menu:newtask")
async def newtask_button(callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await guard(callback.message, db, settings):
        return
    await state.clear()
    await state.set_state(TaskStates.name)
    await edit_or_answer(callback, "New Task\n\nSend a name for this task.", back_keyboard())


@router.message(Command("newtask"))
async def newtask_command(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not await guard(message, db, settings):
        return
    await state.clear()
    await state.set_state(TaskStates.name)
    await message.answer("New Task\n\nSend a name for this task.", reply_markup=back_keyboard())


@router.message(TaskStates.name)
async def task_name(message: Message, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    await state.update_data(task_name=(message.text or "").strip()[:120])
    await state.set_state(TaskStates.source)
    await show_picker(message, state, telethon, "source", db)


def parse_chat_input(value: str) -> str | None:
    value = value.strip()
    if re.fullmatch(r"@[A-Za-z0-9_]{4,}", value):
        return value
    if re.fullmatch(r"https?://t\.me/[A-Za-z0-9_+/.-]+/?", value):
        return value
    return None


async def add_manual_chat(message: Message, state: FSMContext, telethon: TelethonService, db: Database, kind: str) -> None:
    value = parse_chat_input(message.text or "")
    lang = await user_language(db, message)
    if not value:
        await message.answer(t(lang, "invalid_chat"))
        return
    try:
        entity = await telethon.validate_for_user(message.from_user.id, value)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    data = await state.get_data()
    selected = list(data.get(kind, []))
    user = await ensure_user(db, message)
    limit = PLANS[user["plan"]].sources_per_task if kind == "source" else PLANS[user["plan"]].destinations_per_task
    if len(selected) >= limit:
        await message.answer(t(lang, "task_limit", plan=user["plan"].title(), limit=limit, kind=kind))
        return
    if not any(str(item["id"]) == str(entity["id"]) for item in selected):
        selected.append(entity)
    await state.update_data(**{kind: selected})
    if kind == "source":
        await state.set_state(TaskStates.destination)
        await show_picker(message, state, telethon, "destination", db)
    else:
        await finish_task(message, state, db)


@router.message(TaskStates.source)
async def task_source_input(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    await add_manual_chat(message, state, telethon, db, "source")


@router.message(TaskStates.destination)
async def task_destination_input(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    await add_manual_chat(message, state, telethon, db, "destination")


@router.callback_query(F.data.startswith("chat:"))
async def chat_select(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    _, kind, index_text = callback.data.split(":")
    data = await state.get_data()
    chats = data.get("chat_options", [])
    if not (0 <= int(index_text) < len(chats)):
        await callback.answer("Chat list expired. Please open it again.", show_alert=True)
        return
    selected = list(data.get(kind, []))
    user = await ensure_user(db, callback.message)
    plan = PLANS[user["plan"]]
    limit = plan.sources_per_task if kind == "source" else plan.destinations_per_task
    chat = chats[int(index_text)]
    if any(str(item["id"]) == str(chat["id"]) for item in selected):
        selected = [item for item in selected if str(item["id"]) != str(chat["id"])]
    elif len(selected) >= limit:
        await callback.answer(f"{plan.name} allows max {limit} {kind}s.", show_alert=True)
        return
    else:
        selected.append(chat)
    await state.update_data(**{kind: selected})
    await edit_or_answer(callback, t(language_for(user["preferred_language"]), "task_source" if kind == "source" else "task_destination"),
                         chat_picker_keyboard(chats, kind, selected, limit))


@router.callback_query(F.data.startswith("chat_done:"))
async def chat_done(callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService) -> None:
    kind = callback.data.split(":")[1]
    data = await state.get_data()
    selected = data.get(kind, [])
    if not selected:
        await callback.answer("Select at least one chat.", show_alert=True)
        return
    if kind == "source":
        await state.set_state(TaskStates.destination)
        if callback.message:
            await show_picker(callback.message, state, telethon, "destination", db)
    else:
        if callback.message:
            await finish_task(callback.message, state, db)
    await callback.answer()


@router.callback_query(F.data.startswith("chat_manual:"))
async def chat_manual(callback: CallbackQuery, state: FSMContext) -> None:
    kind = callback.data.split(":")[1]
    await state.update_data(manual_kind=kind)
    await callback.answer()
    if callback.message:
        await callback.message.answer("Send @username or https://t.me/username")


async def finish_task(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    user = await ensure_user(db, message)
    plan = PLANS[user["plan"]]
    tasks = await db.list_tasks(user["telegram_user_id"])
    if len(tasks) >= plan.tasks:
        await state.clear()
        await message.answer(f"Your {plan.name} plan allows only {plan.tasks} tasks.", reply_markup=home_keyboard())
        return
    task = await db.create_task(user["telegram_user_id"], data["task_name"], data.get("source", []), data.get("destination", []))
    await state.clear()
    await message.answer(
        t(language_for(user["preferred_language"]), "task_created")
        + f"\nName: {h(task['task_name'])}",
        reply_markup=home_keyboard(),
    )


@router.callback_query(F.data == "menu:tasks")
@router.message(Command("tasks"))
async def tasks_view(event: CallbackQuery | Message, db: Database, settings: Settings) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if not await guard(message, db, settings):
        return
    user = await ensure_user(db, message)
    lang = language_for(user["preferred_language"])
    tasks = await db.list_tasks(user["telegram_user_id"])
    if not tasks:
        text = t(lang, "no_tasks")
        markup = back_keyboard()
    else:
        text = t(lang, "tasks")
        rows = []
        for task in tasks:
            state_text = "Paused" if task["is_paused"] else "Active"
            rows.append([InlineKeyboardButton(text=f"{task['task_name']} · {state_text}", callback_data=f"task:{task['id']}")])
        rows.append([InlineKeyboardButton(text="Home", callback_data="menu:home")])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if isinstance(event, CallbackQuery):
        await edit_or_answer(event, text, markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("task:"))
async def task_actions(callback: CallbackQuery, db: Database) -> None:
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id, callback.from_user.id)
    if not task:
        await callback.answer("Task not found.", show_alert=True)
        return
    action = "Resume" if task["is_paused"] else "Pause"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=action, callback_data=f"task_toggle:{task_id}"),
         InlineKeyboardButton(text="Delete", callback_data=f"task_delete:{task_id}")],
        [InlineKeyboardButton(text="Settings", callback_data=f"task_settings:{task_id}")],
        [InlineKeyboardButton(text="Back", callback_data="menu:tasks")],
    ])
    await edit_or_answer(callback, f"{task['task_name']}\n\nSources: {len(task['sources'])}\nDestinations: {len(task['destinations'])}", markup)


@router.callback_query(F.data.startswith("task_toggle:"))
async def task_toggle(callback: CallbackQuery, db: Database, engine: ForwardingEngine) -> None:
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id, callback.from_user.id)
    if task:
        await db.update_task(task_id, callback.from_user.id, is_paused=not task["is_paused"], pause_reason="user" if not task["is_paused"] else None)
        await engine.refresh_user(callback.from_user.id)
    await callback.answer("Task updated.")
    await callback.message.edit_text("Task updated.", reply_markup=back_keyboard("menu:tasks"))


@router.callback_query(F.data.startswith("task_delete:"))
async def task_delete(callback: CallbackQuery, db: Database, engine: ForwardingEngine) -> None:
    task_id = int(callback.data.split(":")[1])
    await db.delete_task(task_id, callback.from_user.id)
    await engine.refresh_user(callback.from_user.id)
    await callback.answer("Task deleted.")
    await callback.message.edit_text("Task deleted.", reply_markup=back_keyboard("menu:tasks"))


def settings_keyboard(task_id: int, plan_name: str, settings: dict) -> InlineKeyboardMarkup:
    features = [
        ("Header", "header", 0), ("Footer", "footer", 0), ("Media Forward", "media_forward", 0),
        ("URL Preview", "url_preview", 3), ("Remove Usernames", "remove_usernames", 1),
        ("Remove Links", "remove_links", 1), ("Auto Delete", "auto_delete_seconds", 3),
        ("Reply Sync", "reply_sync", 3), ("Blacklist", "blacklist", 2), ("Whitelist", "whitelist", 2),
        ("Replace Words", "replace", 3), ("Delay Timer", "delay_seconds", 1),
        ("Attach File Every Message", "attach_file", 3), ("Replace Matching File", "replace_matching_file", 3),
    ]
    rank = {"free": 0, "silver": 1, "gold": 2, "platinum": 3}.get(plan_name, 0)
    rows = []
    for label, key, required in features:
        locked = rank < required
        value = bool(settings.get(key)) if key not in {"header", "footer", "blacklist", "whitelist", "replace"} else bool(settings.get(key))
        rows.append([InlineKeyboardButton(text=f"{'🔒 ' if locked else ''}{label}: {'ON' if value else 'OFF'}",
                                          callback_data=f"setting:{task_id}:{key}:{'locked' if locked else 'toggle'}")])
    rows.append([InlineKeyboardButton(text="Back", callback_data="menu:tasks")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu:settings")
async def settings_menu(callback: CallbackQuery, db: Database) -> None:
    tasks = await db.list_tasks(callback.from_user.id)
    if not tasks:
        await edit_or_answer(callback, "Create a task first.", back_keyboard())
        return
    rows = [[InlineKeyboardButton(text=task["task_name"], callback_data=f"task_settings:{task['id']}")] for task in tasks]
    rows.append([InlineKeyboardButton(text="Home", callback_data="menu:home")])
    await edit_or_answer(callback, "Select a task to configure:", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("task_settings:"))
async def task_settings(callback: CallbackQuery, db: Database) -> None:
    task_id = int(callback.data.split(":")[1])
    task = await db.get_task(task_id, callback.from_user.id)
    user = await db.get_user(callback.from_user.id)
    if not task or not user:
        await callback.answer("Task not found.", show_alert=True)
        return
    await edit_or_answer(callback, t(language_for(user["preferred_language"]), "settings", task=h(task["task_name"])),
                         settings_keyboard(task_id, user["plan"], task["settings"]))


@router.callback_query(F.data.startswith("setting:"))
async def setting_action(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    _, task_id_text, key, action = callback.data.split(":")
    task_id = int(task_id_text)
    task = await db.get_task(task_id, callback.from_user.id)
    user = await db.get_user(callback.from_user.id)
    if not task or not user:
        await callback.answer("Task not found.", show_alert=True)
        return
    if action == "locked":
        await callback.answer("Upgrade your plan to unlock this feature.", show_alert=True)
        return
    settings = dict(task["settings"] or {})
    if key in {"header", "footer", "blacklist", "whitelist", "replace"}:
        await state.set_state(SettingsStates.value)
        await state.update_data(task_id=task_id, setting_key=key)
        await edit_or_answer(callback, f"Send the new value for {key}. Send /clear to remove it.", back_keyboard(f"task_settings:{task_id}"))
        return
    if key == "auto_delete_seconds" or key == "delay_seconds":
        await state.set_state(SettingsStates.value)
        await state.update_data(task_id=task_id, setting_key=key)
        await edit_or_answer(callback, f"Send seconds for {key}. Send 0 to disable.", back_keyboard(f"task_settings:{task_id}"))
        return
    settings[key] = not bool(settings.get(key))
    await db.update_task(task_id, callback.from_user.id, settings=settings)
    await edit_or_answer(callback, "Setting updated.", settings_keyboard(task_id, user["plan"], settings))


@router.message(SettingsStates.value)
async def setting_value(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    task = await db.get_task(int(data["task_id"]), message.from_user.id)
    if not task:
        await state.clear()
        return
    value = (message.text or "").strip()
    key = data["setting_key"]
    settings = dict(task["settings"] or {})
    if value.lower() == "/clear":
        settings.pop(key, None)
    elif key in {"auto_delete_seconds", "delay_seconds"}:
        if not value.isdigit():
            await message.answer("Send a number of seconds.")
            return
        settings[key] = int(value)
    elif key in {"blacklist", "whitelist"}:
        settings[key] = [item.strip() for item in value.split(",") if item.strip()]
    else:
        settings[key] = value
    await db.update_task(int(data["task_id"]), message.from_user.id, settings=settings)
    await state.clear()
    await message.answer("Setting updated.", reply_markup=home_keyboard())


@router.callback_query(F.data == "menu:language")
@router.message(Command("language"))
async def language_menu(event: CallbackQuery | Message, db: Database) -> None:
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="English", callback_data="language:en"),
         InlineKeyboardButton(text="Hinglish", callback_data="language:hinglish")],
        [InlineKeyboardButton(text="Home", callback_data="menu:home")],
    ])
    if isinstance(event, CallbackQuery):
        await edit_or_answer(event, "Choose your preferred language:", markup)
    else:
        await event.answer("Choose your preferred language:", reply_markup=markup)


@router.callback_query(F.data.startswith("language:"))
async def language_set(callback: CallbackQuery, db: Database) -> None:
    lang = callback.data.split(":")[1]
    await db.set_language(callback.from_user.id, lang)
    await edit_or_answer(callback, t(lang, "language_saved"), main_keyboard(lang))


@router.callback_query(F.data.startswith("menu:faq:"))
@router.message(Command("faq"))
async def faq_view(event: CallbackQuery | Message, db: Database) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    user = await ensure_user(db, message)
    lang = language_for(user["preferred_language"])
    page = int(event.data.split(":")[-1]) if isinstance(event, CallbackQuery) else 0
    items = FAQS[lang]
    per_page = 5
    pages = (len(items) + per_page - 1) // per_page
    page = max(0, min(page, pages - 1))
    rows = [[InlineKeyboardButton(text=f"{i + 1}. {item.question[:52]}", callback_data=f"faq:{page}:{i}")]
            for i, item in enumerate(items[page * per_page:(page + 1) * per_page], page * per_page)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="Prev", callback_data=f"menu:faq:{page-1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Next", callback_data=f"menu:faq:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Home", callback_data="menu:home")])
    text = t(lang, "faq", page=page + 1, pages=pages)
    if isinstance(event, CallbackQuery):
        await edit_or_answer(event, text, InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("faq:"))
async def faq_answer(callback: CallbackQuery, db: Database) -> None:
    _, page_text, index_text = callback.data.split(":")
    user = await db.get_user(callback.from_user.id)
    lang = language_for(user["preferred_language"])
    item = FAQS[lang][int(index_text)]
    await edit_or_answer(callback, t(lang, "faq_answer", question=item.question, answer=item.answer),
                         InlineKeyboardMarkup(inline_keyboard=[
                             [InlineKeyboardButton(text="Back", callback_data=f"menu:faq:{page_text}")],
                             [InlineKeyboardButton(text="Home", callback_data="menu:home")],
                         ]))


@router.callback_query(F.data == "menu:support")
@router.message(Command("support"))
async def support_view(event: CallbackQuery | Message, db: Database, settings: Settings) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    lang = await user_language(db, message)
    text = t(lang, "support", link=settings.support_bot_link or "Please contact the administrator.")
    if isinstance(event, CallbackQuery):
        await edit_or_answer(event, text, back_keyboard())
    else:
        await message.answer(text, reply_markup=back_keyboard())


@router.callback_query(F.data == "menu:refer")
async def refer_view(callback: CallbackQuery) -> None:
    await edit_or_answer(callback, "Share your referral link with friends. Referral rewards are applied using the existing referral rules.", back_keyboard())


@router.message(Command("upload_file", "upload"))
async def upload_file_command(message: Message, state: FSMContext, db: Database) -> None:
    user = await ensure_user(db, message)
    if user["plan"] != "platinum":
        await message.answer("File attachment is available on Platinum.")
        return
    existing = await db.list_user_files(message.from_user.id)
    if existing:
        await message.answer(
            f"You already have an uploaded file: {h(existing[0]['file_name'])}\n"
            "Only one file is allowed. Replace it to upload a new file.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Replace file", callback_data="file_replace")],
                [InlineKeyboardButton(text="Back", callback_data="menu:home")],
            ]),
        )
        return
    await state.set_state(UploadStates.file)
    await message.answer("Send the file you want to store for forwarding.", reply_markup=back_keyboard())


@router.callback_query(F.data == "file_replace")
async def replace_file_prompt(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    user = await db.get_user(callback.from_user.id)
    if not user or user["plan"] != "platinum":
        await callback.answer("File attachment is available on Platinum.", show_alert=True)
        return
    await state.set_state(UploadStates.file)
    await callback.answer()
    await callback.message.edit_text(
        "Send the new file. Your previous file will be removed automatically.",
        reply_markup=back_keyboard(),
    )


@router.message(UploadStates.file, F.document)
async def upload_file(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    user = await ensure_user(db, message)
    if user["plan"] != "platinum":
        await state.clear()
        await message.answer("File attachment is available on Platinum.")
        return
    document = message.document
    max_bytes = 50 * 1024 * 1024
    if document.file_size and document.file_size > max_bytes:
        await state.clear()
        await message.answer("This file is larger than Telegram Bot API's supported limit.")
        return
    if not settings.dummy_storage_channel:
        await state.clear()
        await message.answer("File storage is not configured. Please contact support.")
        return
    try:
        stored = await message.bot.send_document(
            chat_id=settings.dummy_storage_channel,
            document=document.file_id,
            caption=f"Dealskoti file for {message.from_user.id}",
        )
        previous = (await db.list_user_files(message.from_user.id))[:1]
        await db.save_user_file(
            message.from_user.id,
            document.file_id,
            document.file_name or "uploaded_file",
            stored.message_id,
            document.mime_type,
        )
        if previous and previous[0]["dummy_message_id"] and settings.dummy_storage_channel:
            with suppress(Exception):
                await message.bot.delete_message(
                    settings.dummy_storage_channel, previous[0]["dummy_message_id"]
                )
        await message.answer("File saved. You can enable attachment in task settings.", reply_markup=home_keyboard())
    except Exception:
        logger.exception("File upload failed for user %s", message.from_user.id)
        await message.answer("File could not be saved. Please check the storage channel configuration.")
    finally:
        await state.clear()


def is_admin(user_id: int, settings: Settings) -> bool:
    return user_id in settings.admin_telegram_ids


def admin_users_keyboard(users: list, action: str) -> InlineKeyboardMarkup:
    rows = []
    for user in users[:6]:
        label = user["first_name"] or user["username"] or str(user["telegram_user_id"])
        rows.append([InlineKeyboardButton(text=f"{label} ({user['plan']})",
                                          callback_data=f"admin_user:{action}:{user['telegram_user_id']}")])
    rows.append([InlineKeyboardButton(text="Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("admin", "stats", "listusers", "grantdays", "setplan", "block", "unblock", "userinfo", "broadcast"))
async def admin_command(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        await message.answer("This command is restricted to administrators.")
        return
    command = (message.text or "").split()[0].split("@")[0].lower()
    if command == "/stats":
        await message.answer(f"Users: {await db.count_users()}", reply_markup=back_keyboard())
    elif command == "/broadcast":
        await state.set_state(AdminStates.broadcast)
        await message.answer("Send the broadcast message.")
    elif command in {"/grantdays", "/setplan", "/block", "/unblock", "/userinfo"}:
        action = command[1:]
        await message.answer("Select a recent active user:", reply_markup=admin_users_keyboard(await db.list_users(6), action))
    else:
        await message.answer("Admin Panel", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Grant days", callback_data="admin_list:grantdays")],
            [InlineKeyboardButton(text="Block user", callback_data="admin_list:block"),
             InlineKeyboardButton(text="Unblock user", callback_data="admin_list:unblock")],
            [InlineKeyboardButton(text="User info", callback_data="admin_list:userinfo"),
             InlineKeyboardButton(text="Stats", callback_data="admin_stats")],
        ]))


@router.callback_query(F.data.startswith("admin_list:"))
async def admin_list(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer("Admin only.", show_alert=True)
        return
    action = callback.data.split(":")[1]
    await edit_or_answer(callback, "Select a recent active user:", admin_users_keyboard(await db.list_users(6), action))


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer("Admin only.", show_alert=True)
        return
    await edit_or_answer(callback, f"Users: {await db.count_users()}", back_keyboard())


@router.callback_query(F.data.startswith("admin_user:"))
async def admin_user_action(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not is_admin(callback.from_user.id, settings):
        await callback.answer("Admin only.", show_alert=True)
        return
    _, action, user_id_text = callback.data.split(":")
    user_id = int(user_id_text)
    if action == "block":
        await db.set_blocked(user_id, True)
        text = "User blocked."
    elif action == "unblock":
        await db.set_blocked(user_id, False)
        text = "User unblocked."
    elif action == "userinfo":
        user = await db.get_user(user_id)
        text = f"User: {user_id}\nPlan: {user['plan']}\nExpiry: {user['plan_expiry'] or '—'}\nBlocked: {user['is_blocked']}"
    elif action == "grantdays":
        await db.grant_days(user_id, "platinum", 30)
        text = "30 Platinum days granted."
    elif action == "setplan":
        await db.set_plan(user_id, "platinum")
        text = "Plan set to Platinum for 30 days."
    else:
        text = "Unknown admin action."
    await edit_or_answer(callback, text, back_keyboard())


@router.message(AdminStates.broadcast)
async def admin_broadcast(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    if not is_admin(message.from_user.id, settings):
        await state.clear()
        return
    text = message.text or ""
    users = await db.list_users(limit=10000)
    sent = failed = 0
    for user in users:
        try:
            await message.bot.send_message(user["telegram_user_id"], text)
            sent += 1
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(f"Broadcast complete. Sent: {sent}, failed: {failed}.")


async def razorpay_webhook(request: Request) -> JSONResponse:
    billing = request.app.state.billing
    db = request.app.state.db
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not billing.verify_webhook_signature(body, signature):
        return JSONResponse({"ok": False}, status_code=401)
    payment = billing.parse_captured_payment(billing.parse_json(body))
    if not payment:
        return JSONResponse({"ok": True, "ignored": True})
    try:
        await db.activate_payment(payment.order_id, payment.payment_id, payment.amount_paise)
    except ValueError:
        return JSONResponse({"ok": False, "reason": "amount mismatch"}, status_code=400)
    return JSONResponse({"ok": True})


async def health() -> dict:
    return {"ok": True, "service": "dealskoti-forwarder"}


def build_app(settings: Settings, db: Database, billing: RazorpayBilling) -> FastAPI:
    app = FastAPI(title="Dealskoti Forwarder")
    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route(settings.razorpay_webhook_path, razorpay_webhook, methods=["POST"])
    app.state.settings = settings
    app.state.db = db
    app.state.billing = billing
    return app


async def run_async() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = Database(settings.database_url)
    await db.connect()
    telethon = TelethonService(settings, db)
    engine = ForwardingEngine(db, telethon, settings.max_concurrent_forward_tasks)
    billing = RazorpayBilling(settings)
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(db=db, settings=settings, telethon=telethon, engine=engine, billing=billing)
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=cmd[0].lstrip("/"), description=cmd[1]) for cmd in USER_COMMANDS])
    for admin_id in settings.admin_telegram_ids:
        with suppress(Exception):
            await bot.set_my_commands(
                [BotCommand(command=cmd[0].lstrip("/"), description=cmd[1]) for cmd in ADMIN_COMMANDS],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
    await engine.start()
    app = build_app(settings, db, billing)
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(__import__("os").getenv("PORT", "8080")), log_level=settings.log_level.lower()))
    try:
        await asyncio.gather(dp.start_polling(bot), server.serve())
    finally:
        await telethon.cancel_all_logins()
        await engine.stop()
        await bot.session.close()
        await db.close()


def run() -> None:
    try:
        asyncio.run(run_async())
    except ConfigurationError as exc:
        logging.basicConfig(level=logging.ERROR)
        logger.error("Configuration error: %s", exc)
