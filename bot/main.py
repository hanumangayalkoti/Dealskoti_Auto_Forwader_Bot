from __future__ import annotations

import asyncio
import html
import logging
import os
from contextlib import suppress

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

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


class LoginStates(StatesGroup):
    waiting_phone = State()
    waiting_pin = State()
    waiting_2fa = State()


class TaskStates(StatesGroup):
    waiting_name = State()
    waiting_source = State()
    waiting_destination = State()


def _is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids


async def _ensure_user(db: Database, message: Message):
    if message.from_user is None:
        raise RuntimeError("Telegram user is missing")
    return await db.ensure_user(message.from_user.id, message.from_user.username)


async def _language_for_message(db: Database, message: Message):
    user = await _ensure_user(db, message)
    return language_for(user["preferred_language"])


async def _language_for_callback(db: Database, callback: CallbackQuery):
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username)
    return language_for(user["preferred_language"])


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
        [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
        [InlineKeyboardButton(text="💎 Subscription / Plans", callback_data="menu:plans")],
        [InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks")],
        [InlineKeyboardButton(text="👤 My Account", callback_data="menu:account")],
        [InlineKeyboardButton(text="❓ Help / FAQ", callback_data="faq:page:1")],
        [InlineKeyboardButton(text="🌐 Language", callback_data="language:choose")],
    ]
    if settings.support_bot_link:
        buttons.append(
            [InlineKeyboardButton(text="📞 Contact Support", url=settings.support_bot_link)]
        )
    buttons.append(
        [
            InlineKeyboardButton(
                text="📢 Bot Updates Channel",
                url=f"https://t.me/{settings.update_channel_username.lstrip('@')}",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def faq_keyboard(language: str, page: int) -> InlineKeyboardMarkup:
    faqs = FAQS[language_for(language)]
    total_pages = (len(faqs) + 4) // 5
    page = max(1, min(page, total_pages))
    start = (page - 1) * 5
    rows = [
        [
            InlineKeyboardButton(
                text=f"{start + index + 1}. {faq.question}",
                callback_data=f"faq:item:{start + index}",
            )
        ]
        for index, faq in enumerate(faqs[start : start + 5])
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(text="◀️ Back", callback_data=f"faq:page:{page - 1}")
        )
    if page < total_pages:
        navigation.append(
            InlineKeyboardButton(text="Next ▶️", callback_data=f"faq:page:{page + 1}")
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _plan_summary() -> str:
    return "\n".join(
        f"{plan.name}: ₹{plan.monthly_rupees}/month · {plan.tasks} task(s) · "
        f"{plan.daily_messages if plan.daily_messages else 'no normal'} messages/day"
        for plan in PLANS.values()
    )


@router.message(Command("start"))
async def start(message: Message, db: Database, settings: Settings) -> None:
    user = await _ensure_user(db, message)
    language = language_for(user["preferred_language"])
    if not user["language_selected"]:
        await message.answer(t(language, "choose_language"), reply_markup=language_keyboard())
        return
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    await message.answer(t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))


@router.callback_query(F.data.startswith("language:"))
async def choose_language(
    callback: CallbackQuery, db: Database, settings: Settings
) -> None:
    if callback.message is None:
        return
    choice = callback.data.split(":", 1)[1]
    if choice == "choose":
        await callback.message.edit_text(
            "🌐 Choose your language:", reply_markup=language_keyboard()
        )
        await callback.answer()
        return
    if choice not in {"en", "hinglish"}:
        await callback.answer("Invalid language", show_alert=True)
        return
    await db.ensure_user(callback.from_user.id, callback.from_user.username)
    await db.set_language(callback.from_user.id, choice)
    language = language_for(choice)
    await callback.message.edit_text(t(language, "language_saved"))
    if await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.message.answer(
            t(language, "main_menu"), reply_markup=main_menu_keyboard(settings)
        )
    await callback.answer()


@router.callback_query(F.data == "gate:check")
async def check_gate(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username)
    language = language_for(user["preferred_language"])
    if not await user_is_member(callback.bot, settings, callback.from_user.id):
        await callback.answer(t(language, "join_required"), show_alert=True)
        return
    await db.set_membership(callback.from_user.id, True)
    await db.resume_channel_gate_tasks(callback.from_user.id)
    await callback.message.edit_text(t(language, "join_continue"))
    await callback.message.answer(
        t(language, "main_menu"), reply_markup=main_menu_keyboard(settings)
    )
    await callback.answer()


@router.message(Command("help"))
async def help_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "help_title", commands=command_help(language)))


@router.message(Command("adminhelp", "admin"))
async def admin_help_command(
    message: Message, db: Database, settings: Settings
) -> None:
    language = await _language_for_message(db, message)
    if not _is_admin(settings, message.from_user.id):
        await message.answer(t(language, "admin_only"))
        return
    await message.answer(t(language, "admin_help_title", commands=admin_help()))


@router.message(Command("faq"))
async def faq_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(
        t(language, "faq_title", page=1, pages=3),
        reply_markup=faq_keyboard(language, 1),
    )


@router.callback_query(F.data.startswith("faq:page:"))
async def faq_page(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    page = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text(
        t(language, "faq_title", page=page, pages=3),
        reply_markup=faq_keyboard(language, page),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("faq:item:"))
async def faq_item(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    index = int(callback.data.rsplit(":", 1)[1])
    faq = FAQS[language][index]
    await callback.message.edit_text(
        t(language, "faq_answer", question=faq.question, answer=faq.answer),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="◀️ Back to FAQ",
                        callback_data=f"faq:page:{index // 5 + 1}",
                    )
                ],
                [InlineKeyboardButton(text="🏠 Main Menu", callback_data="menu:home")],
            ]
        ),
    )
    await callback.answer()


async def _show_menu(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if await enforce_gate(
        callback.bot, db, settings, callback.from_user.id, language
    ):
        await callback.message.edit_text(
            t(language, "main_menu"), reply_markup=main_menu_keyboard(settings)
        )
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    await _show_menu(callback, db, settings)


@router.callback_query(F.data == "menu:connect")
async def menu_connect(
    callback: CallbackQuery,
    db: Database,
    settings: Settings,
    state: FSMContext,
    telethon: TelethonService,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await telethon.cancel_login(callback.from_user.id)
        await state.set_state(LoginStates.waiting_phone)
        await callback.message.answer(t(language, "login_phone"))
    await callback.answer()


@router.callback_query(F.data == "menu:plans")
async def menu_plans(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    await callback.message.answer(t(language, "plans", plans=_plan_summary()))
    await callback.answer()


@router.callback_query(F.data == "menu:tasks")
async def menu_tasks(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    await callback.message.answer("/tasks")
    await callback.answer()


@router.callback_query(F.data == "menu:account")
async def menu_account(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    user = await db.ensure_user(callback.from_user.id, callback.from_user.username)
    language = language_for(user["preferred_language"])
    session = "connected" if await db.has_active_session(callback.from_user.id) else "not connected"
    expiry = user["plan_expiry"].isoformat() if user["plan_expiry"] else "—"
    await callback.message.answer(
        t(
            language,
            "account",
            plan=user["plan"],
            expiry=expiry,
            session=session,
        )
    )
    await callback.answer()


@router.message(Command("menu"))
async def menu_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        await message.answer(t(language, "main_menu"), reply_markup=main_menu_keyboard(settings))


@router.message(Command("language"))
async def language_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "choose_language"), reply_markup=language_keyboard())


@router.message(Command("support", "contact"))
async def support_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "support", link=settings.support_bot_link or "support"))


@router.message(Command("updates", "channel"))
async def updates_command(message: Message, settings: Settings) -> None:
    await message.answer(
        "📢 Updates Channel",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📢 Join Updates Channel",
                        url=f"https://t.me/{settings.update_channel_username.lstrip('@')}",
                    )
                ]
            ]
        ),
    )


@router.message(Command("account", "myaccount"))
async def account_command(message: Message, db: Database) -> None:
    user = await _ensure_user(db, message)
    language = language_for(user["preferred_language"])
    session = "connected" if await db.has_active_session(message.from_user.id) else "not connected"
    expiry = user["plan_expiry"].isoformat() if user["plan_expiry"] else "—"
    await message.answer(
        t(language, "account", plan=user["plan"], expiry=expiry, session=session)
    )


@router.message(Command("disconnect"))
async def disconnect_command(
    message: Message, db: Database, telethon: TelethonService
) -> None:
    language = await _language_for_message(db, message)
    await telethon.disconnect(message.from_user.id)
    await message.answer(
        "✅ Telegram session disconnected; tasks and plan data were kept."
        if language == "en"
        else "✅ Telegram session disconnect ho gaya; tasks aur plan data safe rakha gaya."
    )


@router.message(Command("connect", "login"))
async def connect_command(
    message: Message,
    state: FSMContext,
    db: Database,
    settings: Settings,
    telethon: TelethonService,
) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    await telethon.cancel_login(message.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await message.answer(t(language, "login_phone"))


@router.message(LoginStates.waiting_phone)
async def login_phone(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(t(language, "login_cancelled"))
        return
    try:
        await telethon.start_phone_login(message.from_user.id, message.text.strip())
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    except Exception:
        await state.clear()
        await message.answer(t(language, "login_failed"))
        return
    await state.set_state(LoginStates.waiting_pin)
    await message.answer(t(language, "login_pin"))


@router.message(LoginStates.waiting_pin)
async def login_pin(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(t(language, "login_cancelled"))
        return
    try:
        result = await telethon.submit_pin(message.from_user.id, message.text.strip())
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    if result == "2fa_required":
        await state.set_state(LoginStates.waiting_2fa)
        await message.answer(t(language, "login_2fa"))
        return
    await state.clear()
    await message.answer(t(language, "login_success"))


@router.message(LoginStates.waiting_2fa)
async def login_2fa(message: Message, state: FSMContext, telethon: TelethonService, db: Database) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await telethon.cancel_login(message.from_user.id)
        await state.clear()
        await message.answer(t(language, "login_cancelled"))
        return
    try:
        await telethon.submit_2fa(message.from_user.id, message.text)
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(t(language, "login_success"))


@router.message(Command("plans"))
async def plans_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "plans", plans=_plan_summary()))


@router.message(Command("subscribe"))
async def subscribe_command(
    message: Message,
    command: CommandObject,
    db: Database,
    settings: Settings,
    billing: RazorpayBilling,
) -> None:
    language = await _language_for_message(db, message)
    args = (command.args or "").split()
    if len(args) != 2 or args[0].lower() not in {"silver", "gold", "platinum"} or args[1].lower() not in {"weekly", "monthly", "yearly"}:
        await message.answer(
            "Usage: /subscribe <silver|gold|platinum> <weekly|monthly|yearly>"
        )
        return
    plan = args[0].lower()
    cycle = args[1].lower()
    first_order = not await db.has_paid_order(message.from_user.id)
    original, discount, payable = payable_amount_paise(
        plan, cycle, first_paid_order=first_order
    )
    try:
        order_id = await billing.create_order(
            amount_paise=payable,
            receipt=f"user_{message.from_user.id}_{plan}_{cycle}",
            plan=plan,
            cycle=cycle,
            user_id=message.from_user.id,
        )
        await db.save_payment(
            message.from_user.id, order_id, plan, cycle, original, discount, payable
        )
    except BillingError:
        await message.answer(t(language, "payment_unavailable"))
        return
    try:
        checkout_url = billing.checkout_url(order_id)
    except BillingError:
        await message.answer(
            "⚠️ Order created, but checkout is not configured. Set PUBLIC_BASE_URL in Railway and retry."
            if language == "en"
            else "⚠️ Order ban gaya, lekin checkout configured nahi hai. Railway me PUBLIC_BASE_URL set karke retry karo."
        )
        return
    await message.answer(
        t(language, "payment_created", amount=format_paise(payable), order_id=order_id)
        + f"\n\n🔗 Checkout: {checkout_url}"
    )


@router.message(Command("tasks", "viewtasks"))
async def view_tasks(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    tasks = await db.list_tasks(message.from_user.id)
    if not tasks:
        await message.answer(t(language, "no_tasks"))
        return
    lines = ["📋 Your tasks:" if language == "en" else "📋 Aapke tasks:"]
    for task in tasks:
        status = "⏸️ paused" if task["is_paused"] else "▶️ active"
        lines.append(f"#{task['id']} — {task['task_name']} — {status}")
    await message.answer("\n".join(lines))


@router.message(Command("newtask", "createtask"))
async def new_task(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    await state.set_state(TaskStates.waiting_name)
    await message.answer(t(language, "task_name"))


@router.message(TaskStates.waiting_name)
async def task_name(message: Message, state: FSMContext, db: Database) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        await message.answer("↩️ Task creation cancelled.")
        return
    await state.update_data(task_name=message.text.strip()[:120])
    await state.set_state(TaskStates.waiting_source)
    await message.answer(t(language, "task_source"))


@router.message(TaskStates.waiting_source)
async def task_source(
    message: Message, state: FSMContext, db: Database, telethon: TelethonService
) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        await message.answer("↩️ Task creation cancelled.")
        return
    try:
        entity = await telethon.validate_for_user(message.from_user.id, message.text.strip())
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.update_data(source=entity)
    await state.set_state(TaskStates.waiting_destination)
    await message.answer(t(language, "task_destination"))


@router.message(TaskStates.waiting_destination)
async def task_destination(
    message: Message, state: FSMContext, db: Database, telethon: TelethonService
) -> None:
    if not message.text:
        return
    language = await _language_for_message(db, message)
    if message.text.strip() == "/back":
        await state.clear()
        await message.answer("↩️ Task creation cancelled.")
        return
    try:
        destination = await telethon.validate_for_user(
            message.from_user.id, message.text.strip()
        )
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    data = await state.get_data()
    task_id = await db.create_task(
        message.from_user.id,
        str(data["task_name"]),
        data["source"],
        destination,
    )
    await state.clear()
    await message.answer(t(language, "task_created", task_id=task_id))


@router.message(Command("pause", "resume", "deletetask"))
async def task_action(message: Message, db: Database) -> None:
    parts = (message.text or "").split()
    command = parts[0].lstrip("/").lower() if parts else ""
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(f"Usage: /{command} <task_id>")
        return
    task_id = int(parts[1])
    if command == "deletetask":
        changed = await db.delete_task(message.from_user.id, task_id)
        result = "🗑️ Task deleted." if changed else "⚠️ Task not found."
    else:
        paused = command == "pause"
        changed = await db.set_task_paused(
            message.from_user.id, task_id, paused, "user" if paused else None
        )
        result = (
            "⏸️ Task paused." if changed and paused else
            "▶️ Task resumed." if changed else "⚠️ Task not found."
        )
    await message.answer(result)


@router.message(Command("stats"))
async def stats_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if not _is_admin(settings, message.from_user.id):
        await message.answer(t(language, "admin_only"))
        return
    stats = await db.stats()
    await message.answer("📊 " + "\n".join(f"{key}: {value}" for key, value in stats.items()))


@router.message()
async def fallback(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "unknown_command"))


def _bot_commands() -> list[BotCommand]:
    return [
        BotCommand(
            command=command.removeprefix("/"),
            description=english[:256],
        )
        for command, english, _ in USER_COMMANDS
    ]


def _admin_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command=command.removeprefix("/"), description=description[:256])
        for command, description in ADMIN_COMMANDS
    ]


def build_app(
    bot: Bot, db: Database, settings: Settings, billing: RazorpayBilling
) -> FastAPI:
    app = FastAPI(
        title="Dealskoti Message Forwarder",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "dealskoti-message-forwarder"}

    @app.get("/checkout/{order_id}", response_class=HTMLResponse)
    async def checkout(order_id: str) -> HTMLResponse:
        payment = await db.get_payment_for_order(order_id)
        if payment is None or payment["status"] != "created":
            return HTMLResponse(
                "<h1>Payment order not found or already processed.</h1>",
                status_code=404,
            )
        key_id = settings.razorpay_key_id
        if not key_id:
            return HTMLResponse("<h1>Payments are not configured.</h1>", status_code=503)
        safe_order = html.escape(order_id, quote=True)
        safe_key = html.escape(key_id, quote=True)
        amount = int(payment["payable_amount_paise"])
        plan = html.escape(str(payment["plan"]).title(), quote=True)
        return HTMLResponse(
            f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Dealskoti — {plan} plan</title>
    <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  </head>
  <body>
    <main style="max-width:420px;margin:15vh auto;padding:24px;font-family:system-ui">
      <h1>Dealskoti</h1>
      <p>Complete your {plan} subscription payment.</p>
      <button id="pay" style="padding:12px 18px;cursor:pointer">Pay ₹{amount / 100:.2f}</button>
      <p id="status" aria-live="polite"></p>
    </main>
    <script>
      const status = document.getElementById("status");
      document.getElementById("pay").onclick = () => {{
        const checkout = new Razorpay({{
          key: "{safe_key}",
          amount: {amount},
          currency: "INR",
          name: "Dealskoti",
          description: "{plan} subscription",
          order_id: "{safe_order}",
          handler: () => {{
            status.textContent = "Payment submitted. Your plan will activate after webhook verification.";
          }},
          modal: {{ ondismiss: () => status.textContent = "Payment window closed." }}
        }});
        checkout.open();
      }};
    </script>
  </body>
</html>"""
        )

    @app.post(settings.razorpay_webhook_path)
    async def razorpay_webhook(request: Request) -> JSONResponse:
        signature = request.headers.get("X-Razorpay-Signature", "")
        raw_body = await request.body()
        if not billing.verify_webhook_signature(raw_body, signature):
            return JSONResponse({"error": "Invalid webhook signature"}, status_code=401)
        try:
            payload = billing.parse_json(raw_body)
            captured = billing.parse_captured_payment(payload)
            if captured is None:
                return JSONResponse({"status": "ignored"})
            stored_payment = await db.get_payment_for_order(captured.order_id)
            if stored_payment is None:
                raise ValueError("Razorpay order was not created by this service")
            stored_plan = str(stored_payment["plan"])
            stored_cycle = str(stored_payment["cycle"])
            user_id = await db.activate_payment(
                captured.order_id,
                captured.payment_id,
                captured.amount_paise,
                duration_days(stored_cycle),
                stored_plan,
                stored_cycle,
            )
        except (BillingError, ValueError) as exc:
            logger.warning("Rejected Razorpay webhook: %s", str(exc))
            return JSONResponse({"error": str(exc)}, status_code=400)
        if user_id is not None:
            user = await db.get_user(user_id)
            if user is not None:
                language = language_for(user["preferred_language"])
                await bot.send_message(
                    user_id,
                    t(
                        language,
                        "payment_success",
                        plan=stored_plan.title(),
                        days=duration_days(stored_cycle),
                    ),
                )
        return JSONResponse({"status": "processed"})

    return app


async def _run(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    db = Database(settings.database_url)
    await db.connect()
    bot = Bot(settings.telegram_bot_token)
    telethon = TelethonService(settings, db)
    billing = RazorpayBilling(settings)
    forwarding = ForwardingEngine(db, telethon, settings.max_concurrent_forward_tasks)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.set_my_commands(_bot_commands())
    for admin_id in settings.admin_telegram_ids:
        await bot.set_my_commands(
            _bot_commands() + _admin_bot_commands(),
            scope=BotCommandScopeChat(chat_id=admin_id),
        )

    api = build_app(bot, db, settings, billing)
    server = uvicorn.Server(
        uvicorn.Config(
            api,
            host="0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            log_level="info",
        )
    )
    dispatcher_task = asyncio.create_task(
        dispatcher.start_polling(bot, db=db, settings=settings, telethon=telethon, billing=billing)
    )
    server_task = asyncio.create_task(server.serve())
    try:
        await forwarding.start()
        forwarding_task = asyncio.create_task(forwarding.run_until_stopped())
        await asyncio.gather(dispatcher_task, server_task, forwarding_task)
    finally:
        server.should_exit = True
        dispatcher_task.cancel()
        if "forwarding_task" in locals():
            forwarding_task.cancel()
        await forwarding.stop()
        await telethon.cancel_all_logins()
        with suppress(asyncio.CancelledError):
            await dispatcher_task
        if "forwarding_task" in locals():
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