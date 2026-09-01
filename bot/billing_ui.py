"""
Plans, pricing and every payment method.

Three ways to pay, all ending at the same place:
  * Razorpay (INR)   — automatic, activated by the webhook in main.py
  * USDT             — admin verifies a TXID
  * Telegram Stars   — admin verifies a screenshot or transaction id

USDT and Stars share ONE table (manual_payments) and ONE admin review screen,
so there is never a second code path to drift out of sync. Approval calls
db.apply_manual_plan(), which runs the exact same upgrade/downgrade maths as a
card payment.

Register in main.py BEFORE the main router:
    from .billing_ui import router as billing_router
    dispatcher.include_router(billing_router)
"""

from __future__ import annotations

import html
import logging
from contextlib import suppress
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .billing import BillingError, RazorpayBilling
from .config import Settings
from .db import Database
from .forwarding import ForwardingEngine
from .gate import enforce_gate
from .locales import language_for, t
from .plans import (
    PLANS,
    duration_days,
    format_paise,
    payable_amount_paise as _payable,
    payable_amount_paise,
    plan_details_text,
    stars_amount,
    usdt_amount_usd,
)

logger = logging.getLogger("dealskoti.billing_ui")

router = Router(name="dealskoti-billing")

CYCLES = ("weekly", "monthly", "yearly")
IST = ZoneInfo("Asia/Kolkata")


# ==========================================
# FSM STATES
# ==========================================

class ManualPayStates(StatesGroup):
    waiting_proof = State()


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


def _nav(back: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Back", callback_data=back)],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])


def _is_admin(settings: Settings, user_id: int) -> bool:
    return user_id in settings.admin_telegram_ids


def _display_name(record) -> str:
    if record is None:
        return "User"
    first = record["first_name"] if "first_name" in record.keys() else None
    username = record["username"] if "username" in record.keys() else None
    return safe_html(first or username or "User")


# ==========================================
# PLAN SCREENS
# ==========================================

def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🥈 Silver", callback_data="plan:silver")],
        [InlineKeyboardButton(text="🥇 Gold", callback_data="plan:gold")],
        [InlineKeyboardButton(text="💎 Platinum", callback_data="plan:platinum")],
        [InlineKeyboardButton(text="🆓 Free", callback_data="plan:free")],
        [InlineKeyboardButton(text="◀️ Back", callback_data="menu:home"),
         InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])


def cycles_keyboard(plan_name: str) -> InlineKeyboardMarkup:
    plan = PLANS[plan_name]
    rows = []
    for cycle in CYCLES:
        _o, _d, payable = payable_amount_paise(plan_name, cycle)
        label = {"weekly": "🗓️ Weekly", "monthly": "📅 Monthly", "yearly": "⭐ Yearly (20% OFF)"}[cycle]
        rows.append([InlineKeyboardButton(
            text=f"{label} — {format_paise(payable)}",
            callback_data=f"cycle:{plan_name}:{cycle}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans")])
    rows.append([
        InlineKeyboardButton(text="◀️ Back", callback_data="menu:home"),
        InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _plans_prefix(user, language: str) -> str:
    if user and user["plan"] != "free":
        current_plan = str(user["plan"]).title()
        expiry = (
            user["plan_expiry"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
            if user["plan_expiry"] else "Lifetime"
        )
        if language == "hinglish":
            return f"👤 <b>Aapka Plan:</b> {current_plan}\n⏳ <b>Expiry:</b> {expiry}\n\n"
        return f"👤 <b>Current Plan:</b> {current_plan}\n⏳ <b>Expiry:</b> {expiry}\n\n"
    label = "Aapka Plan" if language == "hinglish" else "Current Plan"
    return f"👤 <b>{label}:</b> Free\n\n"


@router.message(Command("plans", "subscribe"))
async def plans_command(message: Message, db: Database) -> None:
    language = await _lang(db, message.from_user.id)
    user = await db.get_user(message.from_user.id)
    await message.answer(
        _plans_prefix(user, language) + safe_t(language, "choose_plan"),
        reply_markup=plans_keyboard(), parse_mode="HTML",
    )


@router.callback_query(F.data == "menu:plans")
async def plans_menu_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    language = await _lang(db, callback.from_user.id)
    user = await db.get_user(callback.from_user.id)
    await _show(
        callback.message,
        _plans_prefix(user, language) + safe_t(language, "choose_plan"),
        plans_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("plan:"))
async def plan_details_cb(callback: CallbackQuery, db: Database) -> None:
    """Shows the full feature tree for one plan.

    The body comes from plans.plan_details_text() so the pricing and the
    feature list can never disagree with what the tiers actually unlock.
    """
    if callback.message is None:
        return
    plan_name = callback.data.split(":", 1)[1]
    if plan_name not in PLANS:
        return await callback.answer("Invalid plan", show_alert=True)

    language = await _lang(db, callback.from_user.id)
    details = plan_details_text(plan_name)

    if plan_name == "free":
        await _show(
            callback.message,
            safe_t(language, "plan_details_free", details=details),
            _nav("menu:plans"),
        )
        return await callback.answer()

    await _show(
        callback.message,
        safe_t(language, "plan_details", details=details),
        cycles_keyboard(plan_name),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cycle:"))
async def cycle_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    """Checkout summary with every enabled payment method."""
    if callback.message is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[1], parts[2]
    if plan_name not in PLANS or plan_name == "free" or cycle not in CYCLES:
        return await callback.answer("Invalid option", show_alert=True)

    language = await _lang(db, callback.from_user.id)
    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(plan_name, cycle, first_paid_order=first_order)

    rows = [[InlineKeyboardButton(
        text="💷 Pay with UPI / Card",
        callback_data=f"pay:inr:{plan_name}:{cycle}",
    )]]
    # Each alternative method only appears when it is actually configured, so a
    # user can never start a payment that has nowhere to go.
    if settings.usdt_enabled and usdt_amount_usd(plan_name, cycle) > 0:
        rows.append([InlineKeyboardButton(
            text=f"🪙 Pay with USDT — ${usdt_amount_usd(plan_name, cycle):g}",
            callback_data=f"pay:usdt:{plan_name}:{cycle}",
        )])
    if settings.stars_enabled and stars_amount(plan_name, cycle) > 0:
        rows.append([InlineKeyboardButton(
            text=f"⭐ Pay with Stars — {stars_amount(plan_name, cycle)}",
            callback_data=f"pay:stars:{plan_name}:{cycle}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Back", callback_data=f"plan:{plan_name}")])
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])

    await _show(
        callback.message,
        safe_t(
            language, "billing_details",
            plan=PLANS[plan_name].name, cycle=cycle.title(),
            original=format_paise(original), discount=format_paise(discount),
            payable=format_paise(payable),
        ),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ==========================================
# SHARED PRE-PAYMENT CHECKS
# ==========================================

async def _prepay_guard(callback: CallbackQuery, db: Database, settings: Settings, language: str) -> bool:
    """Channel gate + connected account. Returns True when it is safe to go on."""
    if not await enforce_gate(callback.bot, db, settings, callback.from_user.id, language):
        return False
    if not await db.has_active_session(callback.from_user.id):
        await _show(
            callback.message,
            safe_t(language, "connect_required"),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔌 Connect Account", callback_data="menu:connect")],
                [InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans"),
                 InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
        )
        await callback.answer()
        return False
    return True


# ==========================================
# RAZORPAY (INR)
# ==========================================

@router.callback_query(F.data.startswith("pay:inr:"))
async def pay_inr_cb(
    callback: CallbackQuery, db: Database, billing: RazorpayBilling, settings: Settings,
) -> None:
    if callback.message is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[2], parts[3]
    if plan_name not in PLANS or plan_name == "free" or cycle not in CYCLES:
        return await callback.answer("Invalid option", show_alert=True)

    language = await _lang(db, callback.from_user.id)
    if not await _prepay_guard(callback, db, settings, language):
        return

    first_order = not await db.has_paid_order(callback.from_user.id)
    original, discount, payable = payable_amount_paise(plan_name, cycle, first_paid_order=first_order)
    if payable <= 0:
        return await callback.answer("Invalid option", show_alert=True)

    # Razorpay's reference_id has a hard 40-character limit. Single-letter plan
    # and cycle codes plus a short suffix keep this comfortably under it —
    # spelling them out once pushed it to 41 and Razorpay rejected the link.
    receipt = f"dk_{callback.from_user.id}_{plan_name[0]}{cycle[0]}_{uuid4().hex[:12]}"
    try:
        link = await billing.create_payment_link(
            amount_paise=payable, receipt=receipt, plan=plan_name,
            cycle=cycle, user_id=callback.from_user.id,
        )
        await db.save_payment(
            callback.from_user.id, link.link_id, plan_name, cycle, original, discount, payable,
        )
    except BillingError as exc:
        logger.warning("Payment link creation failed for %s: %s", callback.from_user.id, exc)
        return await callback.answer(
            f"{safe_t(language, 'payment_failed')}\n\n{exc}"[:200], show_alert=True,
        )

    await _show(
        callback.message,
        safe_t(
            language, "payment_link",
            plan=PLANS[plan_name].name, cycle=cycle.title(), amount=format_paise(payable),
        ),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay Now", url=link.short_url)],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"cycle:{plan_name}:{cycle}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()


# ==========================================
# USDT + STARS (ADMIN-VERIFIED)
# ==========================================

@router.callback_query(F.data.startswith("pay:usdt:"))
async def pay_usdt_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[2], parts[3]
    if plan_name not in PLANS or plan_name == "free" or cycle not in CYCLES:
        return await callback.answer("Invalid option", show_alert=True)

    language = await _lang(db, callback.from_user.id)
    if not settings.usdt_enabled:
        return await callback.answer(safe_t(language, "usdt_unavailable"), show_alert=True)
    if not await _prepay_guard(callback, db, settings, language):
        return

    amount = usdt_amount_usd(plan_name, cycle)
    await _show(
        callback.message,
        safe_t(
            language, "usdt_instructions",
            plan=PLANS[plan_name].name, cycle=cycle.title(), amount=f"{amount:g}",
            network=safe_html(settings.usdt_network),
            wallet=safe_html(settings.usdt_wallet_address),
        ),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=safe_t(language, "usdt_paid_btn"),
                callback_data=f"proof:usdt:{plan_name}:{cycle}",
            )],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"cycle:{plan_name}:{cycle}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pay:stars:"))
async def pay_stars_cb(callback: CallbackQuery, db: Database, settings: Settings) -> None:
    if callback.message is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer("Invalid option", show_alert=True)
    plan_name, cycle = parts[2], parts[3]
    if plan_name not in PLANS or plan_name == "free" or cycle not in CYCLES:
        return await callback.answer("Invalid option", show_alert=True)

    language = await _lang(db, callback.from_user.id)
    if not settings.stars_enabled:
        return await callback.answer(safe_t(language, "stars_receiver_missing"), show_alert=True)
    amount = stars_amount(plan_name, cycle)
    if amount <= 0:
        return await callback.answer(safe_t(language, "stars_unavailable"), show_alert=True)
    if not await _prepay_guard(callback, db, settings, language):
        return

    await _show(
        callback.message,
        safe_t(
            language, "stars_instructions",
            plan=PLANS[plan_name].name, cycle=cycle.title(), amount=amount,
            receiver=safe_html(settings.stars_receiver),
        ),
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=safe_t(language, "stars_paid_btn"),
                callback_data=f"proof:stars:{plan_name}:{cycle}",
            )],
            [InlineKeyboardButton(text="◀️ Back", callback_data=f"cycle:{plan_name}:{cycle}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("proof:"))
async def proof_prompt_cb(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    """Asks for the TXID (USDT) or screenshot/transaction id (Stars)."""
    if callback.message is None:
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        return await callback.answer("Invalid option", show_alert=True)
    method, plan_name, cycle = parts[1], parts[2], parts[3]
    if method not in ("usdt", "stars") or plan_name not in PLANS or cycle not in CYCLES:
        return await callback.answer("Invalid option", show_alert=True)

    language = await _lang(db, callback.from_user.id)
    await state.set_state(ManualPayStates.waiting_proof)
    await state.update_data(method=method, plan=plan_name, cycle=cycle)
    prompt_key = "usdt_txid_prompt" if method == "usdt" else "stars_proof_prompt"
    await _show(
        callback.message,
        safe_t(language, prompt_key),
        _nav(f"pay:{method}:{plan_name}:{cycle}"),
    )
    await callback.answer()


@router.message(ManualPayStates.waiting_proof)
async def proof_submit(
    message: Message, state: FSMContext, db: Database, settings: Settings,
) -> None:
    """Stores the pending payment and pushes it to every admin for review.

    Stars proof may be a photo; USDT proof is always text. Either way the row
    lands in manual_payments with status='pending'.
    """
    data = await state.get_data()
    method = data.get("method")
    plan_name = data.get("plan")
    cycle = data.get("cycle")
    if method not in ("usdt", "stars") or plan_name not in PLANS or cycle not in CYCLES:
        await state.clear()
        return

    language = await _lang(db, message.from_user.id)
    text = (message.text or message.caption or "").strip()

    if text.lower() == "/back":
        await state.clear()
        await message.answer(
            safe_t(language, "payment_failed"),
            reply_markup=_nav(f"cycle:{plan_name}:{cycle}"), parse_mode="HTML",
        )
        return

    proof_file_id = None
    if message.photo:
        proof_file_id = message.photo[-1].file_id
    elif message.document:
        proof_file_id = message.document.file_id

    if not text and not proof_file_id:
        # Nothing usable was sent — stay in the state and ask again rather than
        # filing a blank request the admin cannot verify.
        prompt_key = "usdt_txid_prompt" if method == "usdt" else "stars_proof_prompt"
        await message.answer(safe_t(language, prompt_key), parse_mode="HTML")
        return
    if method == "usdt" and not text:
        await message.answer(safe_t(language, "usdt_txid_prompt"), parse_mode="HTML")
        return

    amount = (
        f"{usdt_amount_usd(plan_name, cycle):g} USDT"
        if method == "usdt" else f"{stars_amount(plan_name, cycle)} Stars"
    )

    try:
        request_id = await db.create_manual_payment(
            message.from_user.id, method, plan_name, cycle,
            amount, reference=text or None, proof_file_id=proof_file_id,
        )
    except Exception:
        logger.exception("Could not store manual payment for %s", message.from_user.id)
        await state.clear()
        await message.answer(safe_t(language, "generic_error"), parse_mode="HTML")
        return

    await state.clear()
    await message.answer(
        safe_t(language, "usdt_submitted" if method == "usdt" else "stars_submitted"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back", callback_data="menu:plans"),
             InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
        parse_mode="HTML",
    )

    await _notify_admins_of_payment(message.bot, db, settings, request_id)


# ==========================================
# ADMIN REVIEW
# ==========================================

async def _notify_referrer(bot: Bot, db: Database, credited) -> None:
    """Tells a referrer they just earned. Best-effort — a blocked referrer must
    never stop a payment from being applied."""
    referrer_id = int(credited["referrer_id"])
    language = await _lang(db, referrer_id)
    with suppress(Exception):
        await bot.send_message(
            referrer_id,
            f"🎁 <b>You earned a referral commission!</b>\n\n"
            f"Your total unpaid earnings: <b>{format_paise(int(credited['commission_amount_paise']))}</b>\n\n"
            f"Contact support to request a payout.",
            parse_mode="HTML",
        )


def _review_markup(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Approve", callback_data=f"mp:ok:{request_id}"),
        InlineKeyboardButton(text="❌ Reject", callback_data=f"mp:no:{request_id}"),
    ]])


async def _review_text(db: Database, request) -> str:
    user = await db.get_user(int(request["user_id"]))
    return safe_t(
        "en", "admin_payment_review",
        method=str(request["method"]).upper(),
        name=_display_name(user),
        user_id=request["user_id"],
        plan=str(request["plan"]).title(),
        cycle=str(request["cycle"]).title(),
        amount=safe_html(request["amount"]),
        ref=safe_html(request["reference"] or "— (screenshot attached)"),
    )


async def _notify_admins_of_payment(
    bot: Bot, db: Database, settings: Settings, request_id: int,
) -> None:
    request = await db.get_manual_payment(request_id)
    if request is None:
        return
    text = await _review_text(db, request)
    markup = _review_markup(request_id)
    for admin_id in settings.admin_telegram_ids:
        try:
            if request["proof_file_id"]:
                # Send the screenshot with the details as its caption so the
                # admin can verify without opening anything else.
                await bot.send_photo(
                    admin_id, request["proof_file_id"],
                    caption=text, reply_markup=markup, parse_mode="HTML",
                )
            else:
                await bot.send_message(admin_id, text, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not notify admin %s of payment %s: %s", admin_id, request_id, exc)


@router.message(Command("pending"))
async def pending_command(message: Message, db: Database, settings: Settings) -> None:
    if not _is_admin(settings, message.from_user.id):
        return
    rows = await db.list_pending_manual_payments()
    if not rows:
        await message.answer(safe_t("en", "admin_no_pending"), parse_mode="HTML")
        return
    for request in rows:
        text = await _review_text(db, request)
        markup = _review_markup(int(request["id"]))
        try:
            if request["proof_file_id"]:
                await message.answer_photo(
                    request["proof_file_id"], caption=text,
                    reply_markup=markup, parse_mode="HTML",
                )
            else:
                await message.answer(text, reply_markup=markup, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not render pending payment %s: %s", request["id"], exc)


@router.callback_query(F.data.startswith("mp:"))
async def manual_payment_review_cb(
    callback: CallbackQuery, db: Database, settings: Settings, forwarding: ForwardingEngine,
) -> None:
    """Approve or reject a USDT / Stars payment.

    set_manual_payment_status() only succeeds while the row is still pending,
    so two admins tapping Approve at the same moment cannot grant the plan
    twice — the second one is told it was already handled.
    """
    if not _is_admin(settings, callback.from_user.id):
        return await callback.answer("Admin only", show_alert=True)

    parts = callback.data.split(":")
    if len(parts) != 3:
        return await callback.answer("Invalid option", show_alert=True)
    action, request_id = parts[1], int(parts[2])

    request = await db.get_manual_payment(request_id)
    if request is None:
        return await callback.answer("Not found", show_alert=True)
    if str(request["status"]) != "pending":
        return await callback.answer(safe_t("en", "admin_payment_gone"), show_alert=True)

    user_id = int(request["user_id"])
    plan_name = str(request["plan"])
    cycle = str(request["cycle"])
    language = await _lang(db, user_id)
    method = str(request["method"])

    if action == "no":
        if not await db.set_manual_payment_status(request_id, "rejected", callback.from_user.id):
            return await callback.answer(safe_t("en", "admin_payment_gone"), show_alert=True)
        try:
            await callback.bot.send_message(
                user_id,
                safe_t(
                    language,
                    "usdt_rejected_user" if method == "usdt" else "stars_rejected_user",
                    txid=safe_html(request["reference"] or "—"),
                    ref=safe_html(request["reference"] or "—"),
                ),
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not notify user %s of rejection: %s", user_id, exc)
        await _mark_reviewed(callback, safe_t("en", "admin_payment_rejected"))
        return await callback.answer("Rejected")

    if action != "ok":
        return await callback.answer("Invalid option", show_alert=True)

    # Claim the row FIRST. If this fails somebody else already handled it and
    # we must not touch the plan.
    if not await db.set_manual_payment_status(request_id, "approved", callback.from_user.id):
        return await callback.answer(safe_t("en", "admin_payment_gone"), show_alert=True)

    days = duration_days(cycle)
    try:
        applied = await db.apply_manual_plan(request_id, plan_name, days)
    except Exception:
        logger.exception("Failed to apply manual plan for request %s", request_id)
        applied = None

    if applied is None:
        await callback.answer("Could not apply the plan — check the logs", show_alert=True)
        return

    with_suppress_refresh = getattr(forwarding, "refresh_user", None)
    if with_suppress_refresh is not None:
        try:
            await forwarding.refresh_user(user_id)
        except Exception as exc:
            logger.warning("Could not hot-reload forwarding for %s: %s", user_id, exc)

    # Referral commission — the referrer earns on EVERY payment, not just the
    # first, so this runs for manual approvals exactly as it does for cards.
    with suppress(Exception):
        _o, _d, payable = _payable(plan_name, cycle)
        credited = await db.credit_referral_commission(user_id, payable)
        if credited is not None:
            await _notify_referrer(callback.bot, db, credited)

    user = await db.get_user(user_id)
    expiry = (
        user["plan_expiry"].astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")
        if user and user["plan_expiry"] else "—"
    )
    try:
        await callback.bot.send_message(
            user_id,
            safe_t(
                language,
                "usdt_approved_user" if method == "usdt" else "stars_approved_user",
                plan=PLANS[plan_name].name, days=days, expiry=expiry,
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Could not notify user %s of approval: %s", user_id, exc)

    await _mark_reviewed(
        callback,
        safe_t("en", "admin_payment_approved", plan=PLANS[plan_name].name, user_id=user_id),
    )
    await callback.answer("Approved")


async def _mark_reviewed(callback: CallbackQuery, note: str) -> None:
    """Replaces the review buttons with the outcome, so the same request can
    never be actioned twice from a stale message."""
    if callback.message is None:
        return
    try:
        if callback.message.photo:
            await callback.message.edit_caption(
                caption=(callback.message.caption or "") + f"\n\n{note}", parse_mode="HTML",
            )
        else:
            await callback.message.edit_text(
                (callback.message.html_text or callback.message.text or "") + f"\n\n{note}",
                parse_mode="HTML",
            )
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            logger.debug("Could not annotate reviewed payment: %s", exc)
