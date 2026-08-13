from __future__ import annotations

import asyncio
import html
import logging
import os
import traceback
from contextlib import suppress
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
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


class LoginStates(StatesGroup):
    waiting_phone = State()
    waiting_pin = State()
    waiting_2fa = State()


class TaskStates(StatesGroup):
    waiting_name = State()
    waiting_source = State()
    waiting_destination = State()
    waiting_rename = State()


class TaskSettingsStates(StatesGroup):
    waiting_blacklist = State()
    waiting_whitelist = State()
    waiting_replace = State()
    waiting_header = State()
    waiting_footer = State()
    waiting_auto_delete = State()
    waiting_user_filter = State()


class AdminBroadcastStates(StatesGroup):
    waiting_message = State()


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


@router.errors()
async def global_error_handler(event: ErrorEvent, settings: Settings) -> bool:
    """Catches any exception raised inside a handler so buttons/commands
    never fail silently: the user sees a message, and admins get the
    full traceback so the bug can actually be diagnosed."""
    logger.exception("Unhandled error while processing update", exc_info=event.exception)
    update = event.update
    chat_bot: Bot | None = None
    if update.callback_query is not None:
        chat_bot = update.callback_query.bot
        with suppress(Exception):
            await update.callback_query.answer(
                "⚠️ Something went wrong. Please try again.", show_alert=True
            )
        with suppress(Exception):
            if update.callback_query.message is not None:
                await update.callback_query.message.answer(
                    "⚠️ Something went wrong processing that. Please try again, "
                    "or contact support if it keeps happening."
                )
    elif update.message is not None:
        chat_bot = update.message.bot
        with suppress(Exception):
            await update.message.answer(
                "⚠️ Something went wrong processing that. Please try again, "
                "or contact support if it keeps happening."
            )
    if chat_bot is not None:
        tb = "".join(
            traceback.format_exception(
                type(event.exception), event.exception, event.exception.__traceback__
            )
        )[-3500:]
        with suppress(Exception):
            await _notify_admins(chat_bot, settings, f"🚨 Bot error:\n<pre>{tb}</pre>")
    return True


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
            [
                InlineKeyboardButton(
                    text="Yearly — 20% Off", callback_data=f"cycle:{plan}:yearly"
                )
            ],
            [InlineKeyboardButton(text="◀️ Back to Plans", callback_data="menu:plans")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]
    )


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
    return str(user["first_name"] or user["username"] or user["telegram_user_id"])


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
            InlineKeyboardButton(text="💎 Plans", callback_data="menu:plans"),
        ],
        [
            InlineKeyboardButton(text="📋 My Tasks", callback_data="menu:tasks"),
            InlineKeyboardButton(text="👤 My Account", callback_data="menu:account"),
        ],
        [
            InlineKeyboardButton(text="❓ Help / FAQ", callback_data="faq:page:1"),
            InlineKeyboardButton(text="🌐 Language", callback_data="language:choose"),
        ],
    ]
    if settings.support_bot_link:
        buttons.append([InlineKeyboardButton(text="📞 Contact Support", url=settings.support_bot_link)])
    buttons.append([InlineKeyboardButton(
        text="📢 Bot Updates Channel",
        url=f"https://t.me/{settings.update_channel_username.lstrip('@')}",
    )])
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


async def _render_tasks(message: Message, db: Database, user_id: int) -> None:
    user = await db.get_user(user_id)
    language = language_for(user["preferred_language"]) if user else "en"
    tasks = await db.list_tasks(user_id)
    rows: list[list[InlineKeyboardButton]] = []
    if tasks:
        lines = [t(language, "tasks_title")]
        for task in tasks:
            status = "⏸️ Paused" if task["is_paused"] else "▶️ Active"
            lines.append(f"#{task['id']} — {task['task_name']} — {status}")
            if task["is_paused"]:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"▶️ Resume #{task['id']}",
                            callback_data=f"task:resume:{task['id']}",
                        ),
                        InlineKeyboardButton(
                            text=f"✏️ Edit #{task['id']}",
                            callback_data=f"task:edit:{task['id']}",
                        ),
                        InlineKeyboardButton(
                            text=f"🗑️ Delete #{task['id']}",
                            callback_data=f"task:delete:{task['id']}",
                        ),
                    ]
                )
            else:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"⏸️ Pause #{task['id']}",
                            callback_data=f"task:pause:{task['id']}",
                        ),
                        InlineKeyboardButton(
                            text=f"✏️ Edit #{task['id']}",
                            callback_data=f"task:edit:{task['id']}",
                        ),
                        InlineKeyboardButton(
                            text=f"🗑️ Delete #{task['id']}",
                            callback_data=f"task:delete:{task['id']}",
                        ),
                    ]
                )
        text = "\n".join(lines)
    else:
        text = t(language, "no_tasks_short")
    rows.append([InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("start"))
async def start(message: Message, db: Database, settings: Settings) -> None:
    user, is_new = await db.ensure_user_with_status(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    if is_new and await db.mark_new_user_notified(message.from_user.id):
        await _notify_admins(
            message.bot,
            settings,
            "🆕 New Dealskoti user\n"
            f"Name: {message.from_user.full_name}\n"
            f"Username: @{message.from_user.username or '—'}\n"
            f"Telegram ID: {message.from_user.id}\n"
            f"Language: {user['preferred_language']}\n"
            "Membership: pending verification",
        )
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
    await db.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )
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
    user = await db.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )
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
async def help_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    text = t(language, "help_title", commands=command_help(language))
    if _is_admin(settings, message.from_user.id):
        text += "\n\n" + t(language, "admin_help_title", commands=admin_help())
    await message.answer(text, reply_markup=_nav_keyboard())


@router.message(Command("adminhelp"))
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
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.answer()
        return
    if await db.has_active_session(callback.from_user.id):
        await callback.message.answer(
            "ℹ️ Aap already connected ho. Dobara connect karoge to purana session "
            "replace ho jaayega."
            if language == "hinglish"
            else "ℹ️ You're already connected. Reconnecting will replace your existing session.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔄 Reconnect anyway", callback_data="connect:force"
                        )
                    ],
                    [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
                ]
            ),
        )
        await callback.answer()
        return
    await telethon.cancel_login(callback.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await callback.message.answer(t(language, "login_phone"))
    await callback.answer()


@router.callback_query(F.data == "menu:plans")
async def menu_plans(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    await callback.message.edit_text(t(language, "choose_plan"), reply_markup=plans_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def plan_details(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    plan_name = callback.data.split(":", 1)[1]
    if plan_name not in PLANS:
        await callback.answer("Invalid plan", show_alert=True)
        return
    language = await _language_for_callback(db, callback)
    plan = PLANS[plan_name]
    if plan_name == "free":
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]
        )
        await callback.message.edit_text(
            t(
                language,
                "plan_details",
                plan=plan.name,
                features=_plan_features(plan_name),
                monthly=plan.monthly_rupees,
            ),
            reply_markup=markup,
        )
    else:
        await callback.message.edit_text(
            t(
                language,
                "plan_details",
                plan=plan.name,
                features=_plan_features(plan_name),
                monthly=plan.monthly_rupees,
            ),
            reply_markup=cycles_keyboard(plan_name),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("cycle:"))
async def billing_cycle(
    callback: CallbackQuery, db: Database
) -> None:
    if callback.message is None:
        return
    _, plan_name, cycle = callback.data.split(":")
    if plan_name not in PLANS or cycle not in {"weekly", "monthly", "yearly"}:
        await callback.answer("Invalid billing option", show_alert=True)
        return
    language = await _language_for_callback(db, callback)
    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(
        plan_name, cycle, first_paid_order=first_order
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm Payment",
                    callback_data=f"payment:confirm:{plan_name}:{cycle}",
                )
            ],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"plan:{plan_name}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]
    )
    await callback.message.edit_text(
        t(
            language,
            "billing_details",
            plan=PLANS[plan_name].name,
            cycle=cycle.title(),
            original=format_paise(original),
            discount=format_paise(discount),
            payable=format_paise(payable),
        ),
        reply_markup=markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payment:confirm:"))
async def confirm_payment(
    callback: CallbackQuery,
    db: Database,
    billing: RazorpayBilling,
    settings: Settings,
) -> None:
    if callback.message is None:
        return
    _, _, plan_name, cycle = callback.data.split(":")
    if plan_name not in PLANS or cycle not in {"weekly", "monthly", "yearly"}:
        await callback.answer("Invalid payment option", show_alert=True)
        return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.answer()
        return
    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(
        plan_name, cycle, first_paid_order=first_order
    )
    # Razorpay requires a unique reference_id per payment link. A receipt
    # keyed only on user+plan+cycle collides on every repeat purchase
    # (renewal, retry after a failed webhook, etc.) and Razorpay then
    # refuses to create a fresh link — so a short unique suffix is added
    # to every attempt.
    unique_suffix = uuid4().hex[:10]
    try:
        link = await billing.create_payment_link(
            amount_paise=payable,
            receipt=f"dk_{callback.from_user.id}_{plan_name}_{cycle}_{unique_suffix}",
            plan=plan_name,
            cycle=cycle,
            user_id=callback.from_user.id,
        )
        await db.save_payment(
            callback.from_user.id,
            link.link_id,
            plan_name,
            cycle,
            original,
            discount,
            payable,
        )
    except BillingError:
        await callback.answer(t(language, "payment_failed"), show_alert=True)
        return
    await callback.message.edit_text(
        t(
            language,
            "payment_link",
            plan=PLANS[plan_name].name,
            cycle=cycle.title(),
            amount=format_paise(payable),
        ),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💳 Buy Now", url=link.short_url)],
                [InlineKeyboardButton(text="◀️ Back to Plans", callback_data="menu:plans")],
                [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]
        ),
    )
    await _notify_admins(
        callback.bot,
        settings,
        "🧾 Razorpay Payment Link created\n"
        f"User: {callback.from_user.id}\nPlan: {plan_name.title()}\n"
        f"Cycle: {cycle}\nAmount: {format_paise(payable)}\n"
        f"Payment Link ID: {link.link_id}",
    )
    await callback.answer()


@router.callback_query(F.data == "menu:tasks")
async def menu_tasks(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.answer()
        return
    await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "task:create")
async def task_create_callback(
    callback: CallbackQuery,
    db: Database,
    settings: Settings,
    state: FSMContext,
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        await callback.answer()
        return
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer()
        return
    plan = PLANS.get(str(user["plan"]), PLANS["free"])
    if await db.count_tasks(callback.from_user.id) >= plan.tasks:
        await callback.message.edit_text(
            f"⚠️ {plan.name} plan ki {plan.tasks} task limit reach ho gayi hai."
            if language == "hinglish"
            else f"⚠️ Your {plan.name} plan allows only {plan.tasks} task(s).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
                    [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
                ]
            ),
        )
        await callback.answer()
        return
    await state.set_state(TaskStates.waiting_name)
    await callback.message.edit_text(
        t(language, "task_name"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:pause:"))
async def task_pause_callback(
    callback: CallbackQuery,
    db: Database,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
    task_id = int(callback.data.rsplit(":", 1)[1])
    changed = await db.set_task_paused(
        callback.from_user.id, task_id, True, "user"
    )
    await forwarding.refresh_task(task_id)
    if changed:
        await _notify_admins(
            callback.bot,
            settings,
            f"⏸️ Task paused\nUser ID: {callback.from_user.id}\nTask ID: {task_id}",
        )
    await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer("Task paused" if changed else "Task not found", show_alert=not changed)


@router.callback_query(F.data.startswith("task:resume:"))
async def task_resume_callback(
    callback: CallbackQuery,
    db: Database,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
    task_id = int(callback.data.rsplit(":", 1)[1])
    changed = await db.set_task_paused(
        callback.from_user.id, task_id, False, None
    )
    await forwarding.refresh_task(task_id)
    if changed:
        await _notify_admins(
            callback.bot,
            settings,
            f"▶️ Task resumed\nUser ID: {callback.from_user.id}\nTask ID: {task_id}",
        )
    await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer("Task resumed" if changed else "Task not found", show_alert=not changed)


@router.callback_query(F.data.startswith("task:delete:"))
async def task_delete_confirm(callback: CallbackQuery, db: Database) -> None:
    task_id = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text(
        f"⚠️ Delete task #{task_id} permanently?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Yes, delete",
                        callback_data=f"task:delete-confirm:{task_id}",
                    ),
                    InlineKeyboardButton(
                        text="✖️ Cancel",
                        callback_data="menu:tasks",
                    ),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:delete-confirm:"))
async def task_delete_callback(
    callback: CallbackQuery,
    db: Database,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
    task_id = int(callback.data.rsplit(":", 1)[1])
    changed = await db.delete_task(callback.from_user.id, task_id)
    await forwarding.remove_task(task_id)
    if changed:
        await _notify_admins(
            callback.bot,
            settings,
            f"🗑️ Task deleted\nUser ID: {callback.from_user.id}\nTask ID: {task_id}",
        )
    await _render_tasks(callback.message, db, callback.from_user.id)
    await callback.answer("Task deleted" if changed else "Task not found", show_alert=not changed)


@router.callback_query(F.data.startswith("task:edit:"))
async def task_edit_menu(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.get_task(task_id)
    if task is None or int(task["user_id"]) != callback.from_user.id:
        await callback.answer("Task not found", show_alert=True)
        return
    await callback.message.edit_text(
        f"✏️ Editing task #{task_id} — {task['task_name']}\n\nWhat would you like to change?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Rename", callback_data=f"task:edit-name:{task_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📥 Edit Sources", callback_data=f"task:edit-source:{task_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📤 Edit Destinations",
                        callback_data=f"task:edit-dest:{task_id}",
                    )
                ],
                [InlineKeyboardButton(text="🔙 Back", callback_data="menu:tasks")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:edit-name:"))
async def task_edit_name_start(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.get_task(task_id)
    if task is None or int(task["user_id"]) != callback.from_user.id:
        await callback.answer("Task not found", show_alert=True)
        return
    await state.set_state(TaskStates.waiting_rename)
    await state.update_data(edit_task_id=task_id)
    await callback.message.edit_text(
        f"✏️ Send the new name for task #{task_id} (current: {task['task_name']}).",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]
            ]
        ),
    )
    await callback.answer()


@router.message(TaskStates.waiting_rename)
async def task_edit_name_finish(
    message: Message, state: FSMContext, db: Database
) -> None:
    if not message.text:
        return
    data = await state.get_data()
    task_id = int(data["edit_task_id"])
    new_name = message.text.strip()[:100]
    changed = await db.rename_task(message.from_user.id, task_id, new_name)
    await state.clear()
    await message.answer(
        f"✅ Task #{task_id} renamed to '{new_name}'." if changed else "⚠️ Task not found."
    )


@router.callback_query(F.data.startswith("task:edit-source:"))
async def task_edit_source_start(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.get_task(task_id)
    if task is None or int(task["user_id"]) != callback.from_user.id:
        await callback.answer("Task not found", show_alert=True)
        return
    language = await _language_for_callback(db, callback)
    await state.set_state(TaskStates.waiting_source)
    await state.update_data(edit_task_id=task_id, edit_field="sources", sources=[])
    await callback.message.edit_text(
        f"📥 Editing sources for task #{task_id}. This replaces the existing sources.\n\n"
        + t(language, "task_source"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task:edit-dest:"))
async def task_edit_dest_start(
    callback: CallbackQuery, db: Database, state: FSMContext
) -> None:
    if callback.message is None:
        return
    task_id = int(callback.data.rsplit(":", 1)[1])
    task = await db.get_task(task_id)
    if task is None or int(task["user_id"]) != callback.from_user.id:
        await callback.answer("Task not found", show_alert=True)
        return
    language = await _language_for_callback(db, callback)
    await state.set_state(TaskStates.waiting_destination)
    await state.update_data(edit_task_id=task_id, edit_field="destinations", destinations=[])
    await callback.message.edit_text(
        f"📤 Editing destinations for task #{task_id}. This replaces the existing destinations.\n\n"
        + t(language, "task_destination"),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(
    callback: CallbackQuery, state: FSMContext, db: Database, settings: Settings
) -> None:
    await state.clear()
    language = await _language_for_callback(db, callback)
    if callback.message:
        await callback.message.edit_text(
            t(language, "main_menu"),
            reply_markup=main_menu_keyboard(settings),
        )
    await callback.answer()


@router.callback_query(F.data == "menu:account")
async def menu_account(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    user = await db.ensure_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
    )
    language = language_for(user["preferred_language"])
    session = "connected" if await db.has_active_session(callback.from_user.id) else "not connected"
    expiry = user["plan_expiry"].isoformat() if user["plan_expiry"] else "—"
    tasks = await db.count_tasks(callback.from_user.id)
    usage = await db.daily_usage(callback.from_user.id)
    await callback.message.edit_text(
        t(
            language,
            "account_details",
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
            language=user["preferred_language"],
        ),
        reply_markup=_nav_keyboard(),
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
    tasks = await db.count_tasks(message.from_user.id)
    usage = await db.daily_usage(message.from_user.id)
    await message.answer(
        t(
            language,
            "account_details",
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
            language=user["preferred_language"],
        ),
        reply_markup=_nav_keyboard(),
    )


@router.message(Command("disconnect"))
async def disconnect_command(
    message: Message,
    db: Database,
    telethon: TelethonService,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
    language = await _language_for_message(db, message)
    await forwarding.remove_user(message.from_user.id)
    await telethon.disconnect(message.from_user.id)
    await _notify_admins(
        message.bot,
        settings,
        f"🔌 Telegram account disconnected\nUser ID: {message.from_user.id}",
    )
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
    if await db.has_active_session(message.from_user.id):
        await message.answer(
            "ℹ️ Aap already connected ho. Dobara connect karoge to purana session "
            "replace ho jaayega."
            if language == "hinglish"
            else "ℹ️ You're already connected. Reconnecting will replace your existing session.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔄 Reconnect anyway", callback_data="connect:force"
                        )
                    ],
                    [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
                ]
            ),
        )
        return
    await telethon.cancel_login(message.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await message.answer(t(language, "login_phone"))


@router.callback_query(F.data == "connect:force")
async def connect_force(
    callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService
) -> None:
    if callback.message is None:
        return
    language = await _language_for_callback(db, callback)
    await telethon.cancel_login(callback.from_user.id)
    await state.set_state(LoginStates.waiting_phone)
    await callback.message.edit_text(t(language, "login_phone"))
    await callback.answer()


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
async def login_pin(
    message: Message,
    state: FSMContext,
    telethon: TelethonService,
    db: Database,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
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
    await forwarding.refresh_user(message.from_user.id)
    await _notify_admins(
        message.bot,
        settings,
        f"🔌 Telegram account connected\nUser ID: {message.from_user.id}",
    )
    await message.answer(t(language, "login_success"))


@router.message(LoginStates.waiting_2fa)
async def login_2fa(
    message: Message,
    state: FSMContext,
    telethon: TelethonService,
    db: Database,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
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
    await forwarding.refresh_user(message.from_user.id)
    await _notify_admins(
        message.bot,
        settings,
        f"🔌 Telegram account connected\nUser ID: {message.from_user.id}",
    )
    await message.answer(t(language, "login_success"))


@router.message(Command("plans"))
async def plans_command(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "choose_plan"), reply_markup=plans_keyboard())


@router.message(Command("subscribe"))
async def subscribe_command(
    message: Message, db: Database
) -> None:
    language = await _language_for_message(db, message)
    await message.answer(t(language, "choose_plan"), reply_markup=plans_keyboard())


@router.message(Command("tasks", "viewtasks"))
async def view_tasks(message: Message, db: Database) -> None:
    language = await _language_for_message(db, message)
    tasks = await db.list_tasks(message.from_user.id)
    if not tasks:
        await message.answer(
            t(language, "no_tasks_short"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")],
                    [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
                ]
            ),
        )
        return
    lines = [t(language, "tasks_title")]
    rows: list[list[InlineKeyboardButton]] = []
    for task in tasks:
        status = "⏸️ paused" if task["is_paused"] else "▶️ active"
        lines.append(f"#{task['id']} — {task['task_name']} — {status}")
        rows.append(
            [
                InlineKeyboardButton(
                    text="▶️ Resume" if task["is_paused"] else "⏸️ Pause",
                    callback_data=f"task:{'resume' if task['is_paused'] else 'pause'}:{task['id']}",
                ),
                InlineKeyboardButton(
                    text="✏️ Edit", callback_data=f"task:edit:{task['id']}"
                ),
                InlineKeyboardButton(
                    text="🗑️ Delete", callback_data=f"task:delete:{task['id']}"
                ),
            ]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text="➕ Create New Task", callback_data="task:create")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]
    )
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("newtask", "createtask"))
async def new_task(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    language = await _language_for_message(db, message)
    if not await enforce_gate(message.bot, db, settings, message.from_user.id, language):
        return
    user = await db.get_user(message.from_user.id)
    if user is None:
        return
    plan = PLANS.get(str(user["plan"]), PLANS["free"])
    if await db.count_tasks(message.from_user.id) >= plan.tasks:
        await message.answer(
            f"⚠️ {plan.name} plan ki {plan.tasks} task limit reach ho gayi hai."
            if language == "hinglish"
            else f"⚠️ Your {plan.name} plan allows only {plan.tasks} task(s).",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
                    [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
                ]
            ),
        )
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


def _text_or_forwarded_chat_id(message: Message) -> str | None:
    """Accept either typed @username/ID text, or a forwarded message from
    the source/destination chat (its chat ID is read from the forward)."""
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


@router.message(TaskStates.waiting_source)
async def task_source(
    message: Message,
    state: FSMContext,
    db: Database,
    telethon: TelethonService,
    forwarding: ForwardingEngine,
) -> None:
    text = _text_or_forwarded_chat_id(message)
    if not text:
        return
    language = await _language_for_message(db, message)
    if text == "/back":
        await state.clear()
        await message.answer("↩️ Cancelled.")
        return

    data = await state.get_data()
    sources: list[dict] = list(data.get("sources", []))
    edit_task_id = data.get("edit_task_id")
    is_editing_sources_only = edit_task_id is not None and data.get("edit_field") == "sources"
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        if not sources:
            await message.answer(
                "⚠️ Kam se kam ek source zaroori hai."
                if language == "hinglish"
                else "⚠️ You need at least one source."
            )
            return
        if is_editing_sources_only:
            changed = await db.update_task_sources(
                message.from_user.id, int(edit_task_id), sources
            )
            await state.clear()
            if changed:
                await forwarding.refresh_task(int(edit_task_id))
            await message.answer(
                f"✅ Sources updated for task #{edit_task_id}."
                if changed
                else "⚠️ Task not found."
            )
            return
        await state.update_data(sources=sources, destinations=[])
        await state.set_state(TaskStates.waiting_destination)
        await message.answer(t(language, "task_destination"))
        return

    if len(sources) >= plan.sources_per_task:
        await message.answer(
            f"⚠️ {plan.name} plan me max {plan.sources_per_task} source allowed hai. /done bhejo aage badhne ke liye."
            if language == "hinglish"
            else f"⚠️ Your {plan.name} plan allows max {plan.sources_per_task} source(s). Send /done to continue."
        )
        return

    try:
        entity = await telethon.validate_for_user(message.from_user.id, text)
    except ValueError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    sources.append(entity)
    await state.update_data(sources=sources)
    remaining = plan.sources_per_task - len(sources)
    if remaining > 0:
        await message.answer(
            f"✅ Source #{len(sources)} add ho gaya. Ek aur bhej sakte ho, ya /done ({remaining} aur allowed)."
            if language == "hinglish"
            else f"✅ Source #{len(sources)} added. Send another, or /done ({remaining} more allowed)."
        )
        return

    if is_editing_sources_only:
        changed = await db.update_task_sources(message.from_user.id, int(edit_task_id), sources)
        await state.clear()
        if changed:
            await forwarding.refresh_task(int(edit_task_id))
        await message.answer(
            f"✅ Sources updated for task #{edit_task_id}." if changed else "⚠️ Task not found."
        )
        return

    await state.update_data(destinations=[])
    await state.set_state(TaskStates.waiting_destination)
    await message.answer(t(language, "task_destination"))


@router.message(TaskStates.waiting_destination)
async def task_destination(
    message: Message,
    state: FSMContext,
    db: Database,
    telethon: TelethonService,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
    text = _text_or_forwarded_chat_id(message)
    if not text:
        return
    language = await _language_for_message(db, message)
    if text == "/back":
        await state.clear()
        await message.answer("↩️ Task creation cancelled.")
        return

    data = await state.get_data()
    destinations: list[dict] = list(data.get("destinations", []))
    edit_task_id = data.get("edit_task_id")
    is_editing_destinations_only = (
        edit_task_id is not None and data.get("edit_field") == "destinations"
    )
    user = await db.get_user(message.from_user.id)
    plan = PLANS.get(str(user["plan"]), PLANS["free"]) if user else PLANS["free"]

    if text.lower() == "/done":
        if not destinations:
            await message.answer(
                "⚠️ Kam se kam ek destination zaroori hai."
                if language == "hinglish"
                else "⚠️ You need at least one destination."
            )
            return
    elif len(destinations) >= plan.destinations_per_task:
        await message.answer(
            f"⚠️ {plan.name} plan me max {plan.destinations_per_task} destination allowed hai. /done bhejo aage badhne ke liye."
            if language == "hinglish"
            else f"⚠️ Your {plan.name} plan allows max {plan.destinations_per_task} destination(s). Send /done to continue."
        )
        return
    else:
        try:
            destination = await telethon.validate_for_user(message.from_user.id, text)
        except ValueError as exc:
            await message.answer(f"⚠️ {exc}")
            return
        destinations.append(destination)
        await state.update_data(destinations=destinations)
        remaining = plan.destinations_per_task - len(destinations)
        if remaining > 0:
            await message.answer(
                f"✅ Destination #{len(destinations)} add ho gaya. Ek aur bhej sakte ho, ya /done ({remaining} aur allowed)."
                if language == "hinglish"
                else f"✅ Destination #{len(destinations)} added. Send another, or /done ({remaining} more allowed)."
            )
            return
        # limit reached, fall through to save automatically

    if is_editing_destinations_only:
        changed = await db.update_task_destinations(
            message.from_user.id, int(edit_task_id), destinations
        )
        await state.clear()
        if changed:
            await forwarding.refresh_task(int(edit_task_id))
        await message.answer(
            f"✅ Destinations updated for task #{edit_task_id}."
            if changed
            else "⚠️ Task not found."
        )
        return

    data = await state.get_data()
    task_id = await db.create_task_multi(
        message.from_user.id,
        str(data["task_name"]),
        list(data["sources"]),
        list(data["destinations"]),
    )
    await state.clear()
    await forwarding.refresh_task(task_id)
    await _notify_admins(
        message.bot,
        settings,
        f"➕ New forwarding task\nUser ID: {message.from_user.id}\nTask ID: {task_id}",
    )
    await message.answer(t(language, "task_created", task_id=task_id))


@router.message(Command("pause", "resume", "deletetask"))
async def task_action(
    message: Message,
    db: Database,
    forwarding: ForwardingEngine,
    settings: Settings,
) -> None:
    parts = (message.text or "").split()
    command = parts[0].lstrip("/").lower() if parts else ""
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(f"Usage: /{command} <task_id>")
        return
    task_id = int(parts[1])
    if command == "deletetask":
        changed = await db.delete_task(message.from_user.id, task_id)
        await forwarding.remove_task(task_id)
        result = "🗑️ Task deleted." if changed else "⚠️ Task not found."
    else:
        paused = command == "pause"
        changed = await db.set_task_paused(
            message.from_user.id, task_id, paused, "user" if paused else None
        )
        await forwarding.refresh_task(task_id)
        result = (
            "⏸️ Task paused." if changed and paused else
            "▶️ Task resumed." if changed else "⚠️ Task not found."
        )
    if changed:
        await _notify_admins(
            message.bot,
            settings,
            f"🔧 Task {command}\nUser ID: {message.from_user.id}\nTask ID: {task_id}",
        )
    await message.answer(result)


# Which task-settings keys each plan unlocks. Checked before any setter runs.
TIER_FEATURES: dict[str, set[str]] = {
    "free": set(),
    "silver": {"header", "footer"},
    "gold": {"header", "footer", "blacklist", "whitelist", "replace"},
    "platinum": {
        "header", "footer", "blacklist", "whitelist", "replace",
        "watermark", "auto_delete_seconds", "edit_sync", "user_filter",
    },
}


async def _check_task_feature(
    message: Message, db: Database, task_id: int, feature: str
) -> bool:
    user = await db.get_user(message.from_user.id)
    plan_name = str(user["plan"]) if user else "free"
    if feature not in TIER_FEATURES.get(plan_name, set()):
        await message.answer(
            f"⚠️ Your current plan ({plan_name.title()}) doesn't include this feature. "
            "Use /plans to upgrade."
        )
        return False
    task = await db.get_task(task_id)
    if task is None or int(task["user_id"]) != message.from_user.id:
        await message.answer("⚠️ Task not found.")
        return False
    return True


@router.message(Command("setheader"))
async def set_header_command(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /setheader <task_id> <text> (omit text to clear)")
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "header"):
        return
    header_text = parts[2] if len(parts) > 2 else ""
    await db.update_task_settings(message.from_user.id, task_id, {"header": header_text})
    await message.answer("✅ Header updated." if header_text else "✅ Header cleared.")


@router.message(Command("setfooter"))
async def set_footer_command(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Usage: /setfooter <task_id> <text> (omit text to clear)")
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "footer"):
        return
    footer_text = parts[2] if len(parts) > 2 else ""
    await db.update_task_settings(message.from_user.id, task_id, {"footer": footer_text})
    await message.answer("✅ Footer updated." if footer_text else "✅ Footer cleared.")


@router.message(Command("setblacklist", "setwhitelist"))
async def set_filter_command(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split(maxsplit=2)
    command = parts[0].lstrip("/").lower() if parts else ""
    key = "blacklist" if command == "setblacklist" else "whitelist"
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(f"Usage: /{command} <task_id> <word1,word2,...> (omit to clear)")
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, key):
        return
    words = [w.strip() for w in parts[2].split(",") if w.strip()] if len(parts) > 2 else []
    await db.update_task_settings(message.from_user.id, task_id, {key: words})
    await message.answer(f"✅ {key.title()} updated: {len(words)} word(s).")


@router.message(Command("setreplace"))
async def set_replace_command(message: Message, db: Database, forwarding: ForwardingEngine) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(
            "Usage: /setreplace <task_id> <old1=>new1,old2=>new2> (omit to clear)"
        )
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "replace"):
        return
    mapping: dict[str, str] = {}
    if len(parts) > 2:
        for pair in parts[2].split(","):
            if "=>" not in pair:
                continue
            old, new = pair.split("=>", 1)
            mapping[old.strip()] = new.strip()
    await db.update_task_settings(message.from_user.id, task_id, {"replace": mapping})
    await message.answer(f"✅ Replace rules updated: {len(mapping)} rule(s).")


@router.message(Command("togglewatermark"))
async def toggle_watermark_command(
    message: Message, db: Database, forwarding: ForwardingEngine
) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or parts[2].lower() not in ("on", "off"):
        await message.answer("Usage: /togglewatermark <task_id> <on|off>")
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "watermark"):
        return
    enabled = parts[2].lower() == "on"
    await db.update_task_settings(message.from_user.id, task_id, {"watermark": enabled})
    await message.answer(f"✅ Watermark turned {'on' if enabled else 'off'}.")


@router.message(Command("toggleeditsync"))
async def toggle_edit_sync_command(
    message: Message, db: Database, forwarding: ForwardingEngine
) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or parts[2].lower() not in ("on", "off"):
        await message.answer("Usage: /toggleeditsync <task_id> <on|off>")
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "edit_sync"):
        return
    enabled = parts[2].lower() == "on"
    await db.update_task_settings(message.from_user.id, task_id, {"edit_sync": enabled})
    await message.answer(f"✅ Live edit-sync turned {'on' if enabled else 'off'}.")


@router.message(Command("setautodelete"))
async def set_auto_delete_command(
    message: Message, db: Database, forwarding: ForwardingEngine
) -> None:
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[1].isdigit() or not (parts[2].isdigit() or parts[2].lower() == "off"):
        await message.answer("Usage: /setautodelete <task_id> <seconds|off>")
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "auto_delete_seconds"):
        return
    seconds = 0 if parts[2].lower() == "off" else int(parts[2])
    await db.update_task_settings(
        message.from_user.id, task_id, {"auto_delete_seconds": seconds}
    )
    await message.answer(
        "✅ Auto-delete turned off." if seconds == 0 else f"✅ Auto-delete set to {seconds}s."
    )


@router.message(Command("setuserfilter"))
async def set_user_filter_command(
    message: Message, db: Database, forwarding: ForwardingEngine
) -> None:
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer(
            "Usage: /setuserfilter <task_id> <sender_id1,sender_id2,...> (omit to clear)"
        )
        return
    task_id = int(parts[1])
    if not await _check_task_feature(message, db, task_id, "user_filter"):
        return
    ids: list[int] = []
    if len(parts) > 2:
        for raw in parts[2].split(","):
            raw = raw.strip()
            if raw.lstrip("-").isdigit():
                ids.append(int(raw))
    await db.update_task_settings(message.from_user.id, task_id, {"user_filter": ids})
    await message.answer(f"✅ User filter updated: {len(ids)} sender(s) allowed.")


@router.message(Command("stats"))
async def stats_command(message: Message, db: Database, settings: Settings) -> None:
    language = await _language_for_message(db, message)
    if not _is_admin(settings, message.from_user.id):
        await message.answer(t(language, "admin_only"))
        return
    stats = await db.stats()
    await message.answer(
        "📊 <b>Dealskoti Admin Stats</b>\n\n"
        + "\n".join(f"<b>{key.replace('_', ' ').title()}:</b> {value}" for key, value in stats.items()),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📣 Broadcast", callback_data="admin:broadcast:start")],
                [InlineKeyboardButton(text="🏠 Admin Home", callback_data="admin:home")],
            ]
        ),
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Stats", callback_data="admin:stats"),
                InlineKeyboardButton(text="📣 Broadcast", callback_data="admin:broadcast:start"),
            ],
            [
                InlineKeyboardButton(text="📅 Weekly Report", callback_data="admin:weekly"),
                InlineKeyboardButton(text="👥 Recent Users", callback_data="admin:users"),
            ],
            [InlineKeyboardButton(text="🏠 User Menu", callback_data="menu:home")],
        ]
    )


def _admin_check(settings: Settings, user_id: int) -> bool:
    return _is_admin(settings, user_id)


async def _resolve_target_user(db: Database, raw: str) -> int | None:
    """Admin commands accept either a numeric Telegram user ID or an
    @username — resolve either form to a numeric user ID, or None if the
    user can't be found."""
    raw = raw.strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    user = await db.get_user_by_username(raw)
    return int(user["telegram_user_id"]) if user is not None else None


@router.callback_query(F.data == "admin:home")
async def admin_home(callback: CallbackQuery, settings: Settings) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    if callback.message:
        await callback.message.edit_text("🛠️ <b>Dealskoti Admin Dashboard</b>", parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


@router.message(Command("admin"))
async def admin_dashboard(message: Message, settings: Settings) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    await message.answer("🛠️ <b>Dealskoti Admin Dashboard</b>", parse_mode="HTML", reply_markup=admin_keyboard())


@router.callback_query(F.data == "admin:stats")
async def admin_stats_callback(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    stats = await db.stats()
    text = "📊 <b>Stats</b>\n\n" + "\n".join(
        f"<b>{key.replace('_', ' ').title()}:</b> {value}" for key, value in stats.items()
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


async def _weekly_report(db: Database) -> str:
    stats = await db.stats()
    return (
        "📅 <b>Weekly Dealskoti Report</b>\n\n"
        f"Users: {stats['users']}\n"
        f"New users today: {stats['new_users_today']}\n"
        f"Paid users: {stats['paid_users']}\n"
        f"Active tasks: {stats['active_tasks']}\n"
        f"Captured payments: {stats['captured_payments']}"
    )


@router.callback_query(F.data == "admin:weekly")
async def weekly_report_callback(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    await callback.message.edit_text(await _weekly_report(db), parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin:users")
async def recent_users_callback(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    users = await db.list_users(15)
    lines = ["👥 <b>Recent Users</b>", ""]
    for user in users:
        lines.append(
            f"{user['telegram_user_id']} — "
            f"{user['first_name'] or user['username'] or 'No name'} — "
            f"{str(user['plan']).title()}"
        )
    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_keyboard())
    await callback.answer()


@router.message(Command("broadcast"))
async def broadcast_start(
    message: Message,
    state: FSMContext,
    settings: Settings,
) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    await state.set_state(AdminBroadcastStates.waiting_message)
    await message.answer(
        "📣 Send the broadcast message. It will be previewed before delivery.\n/back to cancel.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]]
        ),
    )


@router.message(AdminBroadcastStates.waiting_message)
async def broadcast_message(
    message: Message, state: FSMContext, settings: Settings
) -> None:
    if not message.text:
        return
    if message.text.strip() == "/back":
        await state.clear()
        await message.answer("Broadcast cancelled.")
        return
    await state.update_data(broadcast_text=message.text[:4000])
    await message.answer(
        "📣 <b>Broadcast Preview</b>\n\n" + html.escape(message.text[:4000]) +
        "\n\nChoose audience:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="All users", callback_data="admin:broadcast:all"),
                    InlineKeyboardButton(text="Active users", callback_data="admin:broadcast:active"),
                ],
                [
                    InlineKeyboardButton(text="Paid users", callback_data="admin:broadcast:paid"),
                    InlineKeyboardButton(text="English", callback_data="admin:broadcast:english"),
                ],
                [
                    InlineKeyboardButton(text="Hinglish", callback_data="admin:broadcast:hinglish"),
                    InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel"),
                ],
            ]
        ),
    )


@router.callback_query(F.data.startswith("admin:broadcast:"))
async def broadcast_send(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
    settings: Settings,
) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    audience = callback.data.rsplit(":", 1)[1]
    if audience == "start":
        await state.set_state(AdminBroadcastStates.waiting_message)
        await callback.message.edit_text(
            "📣 Send the broadcast message. /back to cancel.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")]
                ]
            ),
        )
        await callback.answer()
        return
    data = await state.get_data()
    text = str(data.get("broadcast_text", "")).strip()
    if not text:
        await callback.answer("Broadcast text missing", show_alert=True)
        return
    users = await db.list_broadcast_users(audience)
    broadcast_id = await db.create_broadcast(callback.from_user.id, audience, text, len(users))
    sent = failed = blocked = 0
    await callback.message.edit_text(
        f"📣 Sending to {len(users)} users…", reply_markup=None
    )
    for index, user in enumerate(users, start=1):
        try:
            await callback.bot.send_message(int(user["telegram_user_id"]), text)
            sent += 1
        except TelegramForbiddenError:
            blocked += 1
            await db.mark_user_inactive(int(user["telegram_user_id"]))
        except TelegramBadRequest:
            failed += 1
        if index % 20 == 0:
            await asyncio.sleep(1)
    await db.finish_broadcast(broadcast_id, sent, failed, blocked)
    await state.clear()
    await callback.message.edit_text(
        f"✅ Broadcast complete\n\nRecipients: {len(users)}\nSent: {sent}\nFailed: {failed}\nBlocked: {blocked}",
        reply_markup=admin_keyboard(),
    )
    await callback.answer()


@router.message(Command("block", "unblock"))
async def block_user_command(message: Message, db: Database, settings: Settings, forwarding: ForwardingEngine) -> None:
    language = await _language_for_message(db, message)
    if not _admin_check(settings, message.from_user.id):
        await message.answer(t(language, "admin_only"))
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /block <telegram_user_id or @username>")
        return
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        await message.answer("⚠️ User not found.")
        return
    blocked = parts[0].lower() == "/block"
    changed = await db.set_blocked(user_id, blocked)
    if blocked:
        await forwarding.remove_user(user_id)
    else:
        await forwarding.refresh_user(user_id)
    await message.answer("✅ User updated." if changed else "⚠️ User not found.")


@router.message(Command("grantdays"))
async def grant_days_command(message: Message, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    parts = (message.text or "").split()
    if len(parts) not in (3, 4) or not parts[2].isdigit():
        await message.answer(
            "Usage: /grantdays <telegram_user_id or @username> <days> [plan]\n"
            "If [plan] is omitted, their current plan is extended (or Silver if they're on Free).\n"
            "Tip: use /listusers for a button-based flow instead."
        )
        return
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        await message.answer("⚠️ User not found.")
        return
    if len(parts) == 4:
        plan_key = parts[3].lower()
        if plan_key not in PLANS or plan_key == "free":
            await message.answer("⚠️ Plan must be one of: silver, gold, platinum.")
            return
    else:
        target = await db.get_user(user_id)
        plan_key = str(target["plan"]) if target and target["plan"] != "free" else "silver"
    changed = await db.set_plan(user_id, plan_key, int(parts[2]))
    await message.answer(
        f"✅ {PLANS[plan_key].name} plan extended by {parts[2]} day(s)."
        if changed
        else "⚠️ User not found."
    )


@router.message(Command("setplan"))
async def set_plan_command(message: Message, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    parts = (message.text or "").split()
    if len(parts) != 4 or parts[2].lower() not in PLANS or not parts[3].isdigit():
        await message.answer(
            "Usage: /setplan <telegram_user_id or @username> <free|silver|gold|platinum> <days>"
        )
        return
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        await message.answer("⚠️ User not found.")
        return
    changed = await db.set_plan(user_id, parts[2].lower(), int(parts[3]))
    await message.answer("✅ Plan updated." if changed else "⚠️ User not found.")


class AdminStates(StatesGroup):
    waiting_grant_days = State()


@router.message(Command("listusers"))
async def list_users_command(message: Message, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    users = await db.list_users(15)
    if not users:
        await message.answer("No users found.")
        return
    for u in users:
        label = u["first_name"] or u["username"] or "No name"
        block_label = "✅ Unblock" if u["is_blocked"] else "⛔ Block"
        block_action = "unblock" if u["is_blocked"] else "block"
        await message.answer(
            f"👤 {label}\nID: {u['telegram_user_id']}\nPlan: {u['plan']}\n"
            f"Expiry: {u['plan_expiry'] or '—'}\nBlocked: {'Yes' if u['is_blocked'] else 'No'}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🎁 Grant Days",
                            callback_data=f"admin:grant:{u['telegram_user_id']}",
                        ),
                        InlineKeyboardButton(
                            text=block_label,
                            callback_data=f"admin:{block_action}:{u['telegram_user_id']}",
                        ),
                    ]
                ]
            ),
        )


@router.callback_query(F.data.startswith("admin:grant:"))
async def admin_grant_pick_plan(callback: CallbackQuery, settings: Settings) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    target_user_id = int(callback.data.rsplit(":", 1)[1])
    await callback.message.edit_text(
        f"🎁 Grant which plan to user {target_user_id}?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=plan.name,
                        callback_data=f"admin:grantplan:{target_user_id}:{key}",
                    )
                    for key, plan in PLANS.items()
                    if key != "free"
                ],
                [InlineKeyboardButton(text="✖️ Cancel", callback_data="flow:cancel")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:grantplan:"))
async def admin_grant_pick_days(
    callback: CallbackQuery, settings: Settings, state: FSMContext
) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    _, _, target_user_id, plan_key = callback.data.split(":")
    await state.set_state(AdminStates.waiting_grant_days)
    await state.update_data(target_user_id=int(target_user_id), plan=plan_key)
    await callback.message.edit_text(
        f"📅 {PLANS[plan_key].name} plan chosen for user {target_user_id}.\n"
        "Ab kitne din ke liye grant karna hai? Number bhejo (e.g. 30)."
    )
    await callback.answer()


@router.message(AdminStates.waiting_grant_days)
async def admin_grant_days_finish(
    message: Message, state: FSMContext, db: Database, settings: Settings
) -> None:
    if not _admin_check(settings, message.from_user.id):
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("⚠️ Sirf number bhejo, jaise 30.")
        return
    data = await state.get_data()
    target_user_id = int(data["target_user_id"])
    plan_key = str(data["plan"])
    days = int(message.text.strip())
    changed = await db.set_plan(target_user_id, plan_key, days)
    await state.clear()
    if changed:
        await message.answer(
            f"✅ {PLANS[plan_key].name} granted to user {target_user_id} for {days} day(s)."
        )
        with suppress(TelegramForbiddenError, TelegramBadRequest):
            target = await db.get_user(target_user_id)
            language = language_for(target["preferred_language"]) if target else "en"
            text = (
                f"🎁 Aapko {PLANS[plan_key].name} plan {days} din ke liye mila hai!"
                if language == "hinglish"
                else f"🎁 You've been granted the {PLANS[plan_key].name} plan for {days} day(s)!"
            )
            await message.bot.send_message(target_user_id, text)
    else:
        await message.answer("⚠️ User not found.")


@router.callback_query(F.data.startswith("admin:block:") | F.data.startswith("admin:unblock:"))
async def admin_block_toggle(
    callback: CallbackQuery, db: Database, forwarding: ForwardingEngine, settings: Settings
) -> None:
    if not _admin_check(settings, callback.from_user.id):
        await callback.answer("Admin only", show_alert=True)
        return
    action, target_user_id = callback.data.split(":")[1], int(callback.data.split(":")[2])
    blocked = action == "block"
    changed = await db.set_blocked(target_user_id, blocked)
    if blocked:
        await forwarding.remove_user(target_user_id)
    else:
        await forwarding.refresh_user(target_user_id)
    await callback.answer(
        f"User {'blocked' if blocked else 'unblocked'}." if changed else "User not found.",
        show_alert=True,
    )
    if callback.message is not None and changed:
        with suppress(TelegramBadRequest):
            await callback.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🎁 Grant Days",
                                callback_data=f"admin:grant:{target_user_id}",
                            ),
                            InlineKeyboardButton(
                                text="✅ Unblock" if blocked else "⛔ Block",
                                callback_data=(
                                    f"admin:unblock:{target_user_id}"
                                    if blocked
                                    else f"admin:block:{target_user_id}"
                                ),
                            ),
                        ]
                    ]
                )
            )


@router.message(Command("referralpayout"))
async def referral_payout_command(message: Message, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /referralpayout <referred_user_id or @username>")
        return
    user_id = await _resolve_target_user(db, parts[1])
    if user_id is None:
        await message.answer("⚠️ User not found.")
        return
    result = await db.mark_referral_paid(user_id)
    if result is None:
        await message.answer("⚠️ No unpaid referral commission found for this user.")
        return
    from .plans import format_paise

    await message.answer(
        f"✅ Marked as paid. Referrer: {result['referrer_id']}, "
        f"Amount: {format_paise(int(result['commission_amount_paise']))}"
    )


@router.message(Command("userinfo"))
async def user_info_command(message: Message, db: Database, settings: Settings) -> None:
    if not _admin_check(settings, message.from_user.id):
        await message.answer("⛔ Admin only")
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        await message.answer("Usage: /userinfo <telegram_user_id or @username>")
        return
    user_id = await _resolve_target_user(db, parts[1])
    user = await db.get_user(user_id) if user_id is not None else None
    if user is None:
        await message.answer("⚠️ User not found.")
        return
    await message.answer(
        f"👤 User {user['telegram_user_id']}\n"
        f"Name: {user['first_name'] or '—'}\n"
        f"Username: @{user['username'] or '—'}\n"
        f"Plan: {user['plan']}\n"
        f"Expiry: {user['plan_expiry'] or '—'}\n"
        f"Blocked: {user['is_blocked']}\n"
        f"Last seen: {user['last_seen_at']}"
    )


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

    @app.post(settings.razorpay_webhook_path)
    async def razorpay_webhook(request: Request) -> JSONResponse:
        signature = request.headers.get("X-Razorpay-Signature", "")
        raw_body = await request.body()
        if not billing.verify_webhook_signature(raw_body, signature):
            await _notify_admins(
                bot,
                settings,
                "🚨 Invalid Razorpay webhook signature rejected",
            )
            return JSONResponse({"error": "Invalid webhook signature"}, status_code=401)
        try:
            payload = billing.parse_json(raw_body)
            captured = billing.parse_captured_payment(payload)
            if captured is None:
                return JSONResponse({"status": "ignored"})
            stored_payment = await db.get_payment_for_order(captured.order_id)
            if stored_payment is None:
                await _notify_admins(
                    bot,
                    settings,
                    f"🚨 Unknown Razorpay payment link/order: {captured.order_id}",
                )
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
                expiry = user["plan_expiry"]
                expiry_str = expiry.strftime("%d %b %Y, %I:%M %p") if expiry else "—"
                await bot.send_message(
                    user_id,
                    t(
                        language,
                        "payment_success",
                        plan=stored_plan.title(),
                        days=duration_days(stored_cycle),
                        amount=format_paise(captured.amount_paise),
                        txn_id=captured.payment_id,
                        expiry=expiry_str,
                    ),
                )
                await _notify_admins(
                    bot,
                    settings,
                    "✅ Verified Razorpay payment activated\n"
                    f"User: {user_id}\nPlan: {stored_plan.title()}\n"
                    f"Cycle: {stored_cycle}\nAmount: {format_paise(captured.amount_paise)}\n"
                    f"Payment ID: {captured.payment_id}\n"
                    f"Payment Link ID: {captured.order_id}\n"
                    f"Expiry: {user['plan_expiry']}",
                )
        return JSONResponse({"status": "processed"})

    return app


async def _membership_monitor(
    bot: Bot,
    db: Database,
    settings: Settings,
    forwarding: ForwardingEngine,
) -> None:
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
                    await _notify_admins(
                        bot,
                        settings,
                        f"✅ User rejoined Updates Channel\nUser ID: {user_id}",
                    )
                else:
                    await db.mark_channel_gate_paused_tasks(user_id)
                    await forwarding.remove_user(user_id)
                    await _notify_admins(
                        bot,
                        settings,
                        f"⚠️ User left Updates Channel; forwarding paused\nUser ID: {user_id}",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Membership monitor iteration failed")
        await asyncio.sleep(300)


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
        dispatcher.start_polling(
            bot,
            db=db,
            settings=settings,
            telethon=telethon,
            billing=billing,
            forwarding=forwarding,
        )
    )
    server_task = asyncio.create_task(server.serve())
    membership_task = asyncio.create_task(
        _membership_monitor(bot, db, settings, forwarding)
    )
    try:
        timezone = ZoneInfo(settings.default_timezone)
    except Exception:
        timezone = ZoneInfo("UTC")
    scheduler = AsyncIOScheduler(timezone=timezone)

    async def send_weekly_report() -> None:
        await _notify_admins(bot, settings, await _weekly_report(db))

    async def send_expiry_reminders() -> None:
        for days_ahead in (3, 1):
            for row in await db.get_expiring_users(days_ahead):
                language = str(row["preferred_language"] or "en")
                plan_name = str(row["plan"]).title()
                text = (
                    f"⏳ Tumhara {plan_name} plan {days_ahead} din me expire ho raha hai. "
                    "Renew karne ke liye /plans use karo."
                    if language == "hinglish"
                    else f"⏳ Your {plan_name} plan expires in {days_ahead} day(s). "
                    "Use /plans to renew."
                )
                with suppress(TelegramForbiddenError, TelegramBadRequest):
                    await bot.send_message(int(row["telegram_user_id"]), text)

    async def downgrade_expired_plans() -> None:
        downgraded = await db.downgrade_expired_users()
        for row in downgraded:
            language = str(row["preferred_language"] or "en")
            text = (
                "❌ Tumhara plan expire ho gaya hai aur Free tier pe downgrade ho gaya hai. "
                "Dobara subscribe karne ke liye /plans use karo."
                if language == "hinglish"
                else "❌ Your plan has expired and you've been downgraded to Free. "
                "Use /plans to resubscribe."
            )
            with suppress(TelegramForbiddenError, TelegramBadRequest):
                await bot.send_message(int(row["telegram_user_id"]), text)

    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=timezone),
        id="weekly-admin-report",
        replace_existing=True,
    )
    scheduler.add_job(
        send_expiry_reminders,
        CronTrigger(hour=10, minute=0, timezone=timezone),
        id="plan-expiry-reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        downgrade_expired_plans,
        CronTrigger(hour="*", minute=5, timezone=timezone),
        id="plan-auto-downgrade",
        replace_existing=True,
    )
    scheduler.start()
    try:
        await forwarding.start()
        forwarding_task = asyncio.create_task(forwarding.run_until_stopped())
        await asyncio.gather(dispatcher_task, server_task, forwarding_task)
    finally:
        server.should_exit = True
        dispatcher_task.cancel()
        membership_task.cancel()
        scheduler.shutdown(wait=False)
        if "forwarding_task" in locals():
            forwarding_task.cancel()
        await forwarding.stop()
        await telethon.cancel_all_logins()
        with suppress(asyncio.CancelledError):
            await dispatcher_task
        with suppress(asyncio.CancelledError):
            await membership_task
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
