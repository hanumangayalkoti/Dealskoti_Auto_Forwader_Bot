"""
Task settings interface — every per-task feature toggle lives here.

This is deliberately a SEPARATE module from main.py:
  * main.py owns onboarding, tasks, billing and admin
  * settings_ui.py owns the whole /settings tree

Adding a new feature should only ever touch two places:
  1. plans.py  — the feature constant + which tiers get it
  2. SETTING_SPECS below — one entry describing how it is edited

Nothing in this file hardcodes a plan name. Access is always decided by
plans.plan_has(), so moving a feature between tiers never requires editing
this file.

Register the router in main.py with:
    from .settings_ui import router as settings_router
    dispatcher.include_router(settings_router)
Include it BEFORE the main router so its callbacks are matched first.
"""

from __future__ import annotations

import html
import json
import logging
import re
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .db import Database
from .forwarding import (
    ANTIBAN_PRESETS,
    CODE_FILTER_MODES,
    DEFAULT_REACTION_EMOJI,
    DELAY_PRESETS,
    code_filter_mode,
    ForwardingEngine,
    WATERMARK_OPACITIES,
    WATERMARK_POSITIONS,
    WATERMARK_SIZES,
    raw_peer_id,
)
from .locales import language_for, t
from .plans import (
    PLANS,
    F_ANTIBAN,
    F_ATTACH_FILE,
    F_AUTO_DELETE,
    F_AUTO_REACTION,
    F_BLACKLIST,
    F_DELAY_TIMER,
    F_FOOTER,
    F_HEADER,
    F_HIDDEN_LINKS,
    F_LINK_PREVIEW,
    F_MONO_TEXT,
    F_PER_TARGET_HF,
    F_POST_EDIT_SYNC,
    F_REMOVE_LINKS,
    F_REMOVE_USERNAMES,
    F_REPLACE_LINKS,
    F_REPLACE_USERNAMES,
    F_REPLACE_WORDS,
    F_SENDER_FILTER,
    F_TOPICS,
    F_TRIM_WORDS,
    F_WATERMARK_IMAGE,
    F_WATERMARK_STYLE,
    F_WHITELIST,
    min_plan_for,
    plan_has,
)
from .telethon_service import TelethonService

logger = logging.getLogger("dealskoti.settings")

router = Router(name="dealskoti-settings")


# ==========================================
# FSM STATES
# ==========================================

class SettingsFlow(StatesGroup):
    waiting_value = State()


# ==========================================
# LOCAL HELPERS
# ==========================================
# Deliberately duplicated from main.py rather than imported, so this module
# has no circular dependency on main and can be reasoned about on its own.

def safe_html(text) -> str:
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


def _nav(back: str, task_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="◀️ Back", callback_data=back)]]
    rows.append([
        InlineKeyboardButton(text="◀️ Back", callback_data="menu:home"),
        InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ctx(db: Database, user_id: int, task_id: int):
    """Loads (task, plan_name, language, settings) or (None, ...) if the task
    does not exist or does not belong to this user."""
    task = await db.get_task(task_id)
    if not task or int(task["user_id"]) != user_id:
        return None, "free", "en", {}
    user = await db.get_user(user_id)
    plan_name = str(user["plan"] or "free") if user else "free"
    language = language_for(user["preferred_language"]) if user else "en"
    settings = _json_field(task["settings"], {})
    return task, plan_name, language, settings


async def _show(message_obj, text: str, markup: InlineKeyboardMarkup) -> None:
    """edit_text where possible, otherwise send a new message. Swallows the
    harmless 'message is not modified' error from double taps."""
    if hasattr(message_obj, "edit_text") and getattr(message_obj, "message_id", None):
        try:
            await message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
    await message_obj.answer(text, reply_markup=markup, parse_mode="HTML")


# ==========================================
# SETTING SPECIFICATIONS
# ==========================================
# kind:
#   toggle  — boolean on/off
#   text    — free text (header, footer, watermark text)
#   list    — comma-separated values -> list
#   map     — "old=new, old2=new2" -> dict
#   number  — non-negative integer
#   choice  — one of a fixed set of options
#
# default: value assumed when the key has never been set.

CAT_MESSAGES = "msg"
CAT_CLEANUP = "cln"
CAT_FILTERS = "flt"
CAT_REPLACE = "rep"
CAT_MEDIA = "med"
CAT_FORWARDING = "fwd"
CAT_REACTION = "rct"
CAT_TOPICS = "top"
CAT_CHANNELS = "chn"


class Spec:
    def __init__(
        self, key: str, label: str, category: str, kind: str, feature: str,
        prompt: str | None = None, default=None, choices: tuple = (),
        display: str | None = None,
    ):
        self.key = key
        self.label = label
        self.category = category
        self.kind = kind
        self.feature = feature
        self.prompt = prompt
        self.default = default
        self.choices = choices
        self.display = display or label


SETTING_SPECS: dict[str, Spec] = {}


def _spec(*args, **kwargs) -> None:
    s = Spec(*args, **kwargs)
    SETTING_SPECS[s.key] = s


# --- Messages ---
_spec("header", "📝 Header Text", CAT_MESSAGES, "text", F_HEADER, "header_prompt", "")
_spec("footer", "📝 Footer Text", CAT_MESSAGES, "text", F_FOOTER, "footer_prompt", "")
_spec("per_target_hf", "🎯 Header/Footer Per Target", CAT_MESSAGES, "per_target",
      F_PER_TARGET_HF, "per_target_hf_prompt", {})

# --- Text cleanup ---
_spec("link_preview", "🔗 Link Preview", CAT_CLEANUP, "toggle", F_LINK_PREVIEW,
      "link_preview_prompt", True)
_spec("remove_usernames", "🙈 Remove Usernames", CAT_CLEANUP, "toggle", F_REMOVE_USERNAMES,
      "remove_usernames_prompt", False)
_spec("remove_links", "🚫 Remove Links", CAT_CLEANUP, "toggle", F_REMOVE_LINKS,
      "remove_links_prompt", False)
_spec("disable_hidden_links", "🕵️ Disable Hidden Links", CAT_CLEANUP, "toggle",
      F_HIDDEN_LINKS, "hidden_links_prompt", False)
_spec("trim_words", "✂️ Trim Words/Lines", CAT_CLEANUP, "list", F_TRIM_WORDS,
      "trim_words_prompt", [])

# --- Filters ---
_spec("blacklist", "⛔ Blacklist Keywords", CAT_FILTERS, "list", F_BLACKLIST,
      "blacklist_prompt", [])
_spec("whitelist", "✅ Whitelist Keywords", CAT_FILTERS, "list", F_WHITELIST,
      "whitelist_prompt", [])
_spec("user_filter", "👤 Sender Filter", CAT_FILTERS, "senders", F_SENDER_FILTER,
      "userfilter_prompt", [])
# Code Filter lives with the other filters because it decides WHICH messages get
# forwarded, not how their text is styled.
_spec("mono_text", "🎁 Code Filter", CAT_FILTERS, "choice", F_MONO_TEXT,
      "mono_text_prompt", "off", tuple(CODE_FILTER_MODES), display="Code Filter")

# --- Replacements ---
_spec("replace_words", "🔤 Replace Words", CAT_REPLACE, "map", F_REPLACE_WORDS,
      "replace_prompt", {})
_spec("replace_usernames", "👤 Replace Usernames", CAT_REPLACE, "map", F_REPLACE_USERNAMES,
      "replace_usernames_prompt", {})
_spec("replace_links", "🔗 Replace Links", CAT_REPLACE, "map", F_REPLACE_LINKS,
      "replace_links_prompt", {})

# --- Media & watermark ---
_spec("watermark", "💧 Image Watermark", CAT_MEDIA, "toggle", F_WATERMARK_IMAGE, None, False)
_spec("watermark_text", "✏️ Watermark Text", CAT_MEDIA, "text", F_WATERMARK_IMAGE,
      "watermark_text_prompt", "")
_spec("wm_position", "📍 Watermark Position", CAT_MEDIA, "choice", F_WATERMARK_STYLE,
      "watermark_position_prompt", "bottom_right", tuple(WATERMARK_POSITIONS))
_spec("wm_size", "🔎 Watermark Size", CAT_MEDIA, "choice", F_WATERMARK_STYLE,
      "watermark_size_prompt", "medium", tuple(WATERMARK_SIZES.keys()))
_spec("wm_opacity", "🌫️ Watermark Opacity", CAT_MEDIA, "choice", F_WATERMARK_STYLE,
      "watermark_opacity_prompt", "70", tuple(str(o) for o in WATERMARK_OPACITIES))
_spec("attach_stored_file", "📎 Attach Custom File", CAT_MEDIA, "toggle", F_ATTACH_FILE,
      None, False)

# --- Forwarding behaviour ---
_spec("auto_delete_seconds", "🗑️ Auto Delete Messages", CAT_FORWARDING, "number",
      F_AUTO_DELETE, "autodelete_prompt", 0)
_spec("post_edit_sync", "🔄 Post Edit Sync", CAT_FORWARDING, "toggle", F_POST_EDIT_SYNC,
      "editsync_prompt", False)
_spec("delay_timer", "⏱️ Delay Timer Per Target", CAT_FORWARDING, "choice", F_DELAY_TIMER,
      "delay_timer_prompt", "off", tuple(DELAY_PRESETS.keys()))
_spec("antiban_speed", "🛡️ Anti-Ban Speed", CAT_FORWARDING, "choice", F_ANTIBAN,
      "antiban_prompt", "off", tuple(ANTIBAN_PRESETS.keys()))

# --- Auto reaction ---
_spec("auto_reaction", "😍 Auto Reaction", CAT_REACTION, "reaction", F_AUTO_REACTION,
      "reaction_prompt", {})

# --- Topics ---
_spec("topics", "🧵 Topics Forwarding", CAT_TOPICS, "topics", F_TOPICS, "topics_prompt", {})


CATEGORY_ORDER = [
    (CAT_MESSAGES, "💬 Message Settings", "settings_cat_messages"),
    (CAT_CLEANUP, "🧹 Text Cleanup", "settings_cat_cleanup"),
    (CAT_FILTERS, "🔍 Filters", "settings_cat_filters"),
    (CAT_REPLACE, "🔁 Replacements", "settings_cat_replace"),
    (CAT_MEDIA, "🖼️ Media & Watermark", "settings_cat_media"),
    (CAT_FORWARDING, "🚀 Forwarding", "settings_cat_forwarding"),
    (CAT_REACTION, "😍 Auto Reaction", "settings_cat_reaction"),
    (CAT_TOPICS, "🧵 Topics Forwarding", "settings_cat_topics"),
    (CAT_CHANNELS, "📥 Source/Target Channels", "settings_cat_channels"),
]

# Human-friendly names for choice values, so the UI never shows raw keys.
CHOICE_LABELS = {
    "off": "Off", "fast": "Fast", "normal": "Normal", "slow": "Slow",
    "bottom_right": "Bottom Right", "bottom_left": "Bottom Left",
    "top_right": "Top Right", "top_left": "Top Left", "center": "Center",
    "small": "Small", "medium": "Medium", "large": "Large",
    "mono": "🔠 Monospace only", "spoiler": "🫥 Spoiler only", "both": "🔠+🫥 Both",
    "30": "30%", "50": "50%", "70": "70%", "100": "100%",
}

REACTION_EMOJIS = ["👍", "❤️", "🔥", "🎉", "😍", "👏", "⚡", "💯"]


def _label_for(value) -> str:
    return CHOICE_LABELS.get(str(value), str(value).title())


def _current_value(settings: dict, spec: Spec):
    """Reads a setting, falling back to its declared default.

    Watermark style lives in one nested dict so forwarding.py can read it as a
    unit; the UI still edits the three parts separately.
    """
    if spec.key in ("wm_position", "wm_size", "wm_opacity"):
        style = settings.get("watermark_style")
        style = style if isinstance(style, dict) else {}
        part = {"wm_position": "position", "wm_size": "size", "wm_opacity": "opacity"}[spec.key]
        return str(style.get(part, spec.default))
    if spec.key == "mono_text":
        # Stored as a boolean on older tasks; normalise so the picker shows a
        # tick against the right option instead of nothing.
        return code_filter_mode(settings)
    value = settings.get(spec.key)
    return spec.default if value is None else value


def _summary(settings: dict, spec: Spec) -> str:
    """One-line preview of a setting's value, shown on its button."""
    value = _current_value(settings, spec)
    if spec.kind == "toggle":
        return "✅ ON" if value else "❌ OFF"
    if spec.kind == "choice":
        return _label_for(value)
    if spec.kind == "number":
        return f"{value}s" if value else "Off"
    if spec.kind in ("list", "senders"):
        items = value if isinstance(value, list) else []
        return f"{len(items)} set" if items else "—"
    if spec.kind in ("map", "per_target"):
        items = value if isinstance(value, dict) else {}
        return f"{len(items)} set" if items else "—"
    if spec.kind == "reaction":
        cfg = value if isinstance(value, dict) else {}
        return f"{cfg.get('emoji', DEFAULT_REACTION_EMOJI)} ON" if cfg.get("enabled") else "❌ OFF"
    if spec.kind == "topics":
        cfg = value if isinstance(value, dict) else {}
        total = sum(len(v) for v in cfg.values() if isinstance(v, list))
        return f"{total} selected" if total else "All topics"
    if spec.kind == "text":
        text = str(value or "")
        if not text:
            return "—"
        return (text[:18] + "…") if len(text) > 18 else text
    return "—"


# ==========================================
# ENTRY POINTS
# ==========================================

async def _task_list_markup(db: Database, user_id: int) -> InlineKeyboardMarkup | None:
    tasks = await db.list_tasks(user_id)
    if not tasks:
        return None
    rows = [
        [InlineKeyboardButton(text=f"⚙️ {t_['task_name']}", callback_data=f"st:task:{t_['id']}")]
        for t_ in tasks
    ]
    rows.append([InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("setting", "settings"))
async def settings_command(message: Message, db: Database) -> None:
    user = await db.get_user(message.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    markup = await _task_list_markup(db, message.from_user.id)
    if markup is None:
        await message.answer(
            safe_t(language, "settings_no_tasks"),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ New Task", callback_data="task:create")],
                [InlineKeyboardButton(text="◀️ Back", callback_data="menu:home"),
                 InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )
        return
    await message.answer(safe_t(language, "settings_select_task"), reply_markup=markup, parse_mode="HTML")


@router.callback_query(F.data == "menu:settings")
async def settings_menu_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    user = await db.get_user(callback.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    markup = await _task_list_markup(db, callback.from_user.id)
    if markup is None:
        await _show(
            callback.message, safe_t(language, "settings_no_tasks"),
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ New Task", callback_data="task:create")],
                [InlineKeyboardButton(text="◀️ Back", callback_data="menu:home"),
                 InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
        )
        return await callback.answer()
    await _show(callback.message, safe_t(language, "settings_select_task"), markup)
    await callback.answer()


@router.callback_query(F.data.startswith("st:task:"))
async def settings_task_menu(callback: CallbackQuery, db: Database) -> None:
    """The per-task category menu. Categories the plan cannot use at all are
    shown with a lock rather than hidden, so users can see what upgrading buys
    instead of wondering where a feature went."""
    if callback.message is None:
        return
    task_id = int(callback.data.split(":")[2])
    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)

    sources = _json_field(task["sources"], [])
    destinations = _json_field(task["destinations"], [])
    src_names = ", ".join(safe_html(s.get("title") or s.get("id") or "?") for s in sources) or "—"
    dst_names = ", ".join(safe_html(d.get("title") or d.get("id") or "?") for d in destinations) or "—"
    status_line = "⏸️ Paused" if task["is_paused"] else "▶️ Active"

    rows = []
    for cat, label, _key in CATEGORY_ORDER:
        if cat == CAT_CHANNELS:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"st:cat:{task_id}:{cat}")])
            continue
        specs = [s for s in SETTING_SPECS.values() if s.category == cat]
        unlocked = any(plan_has(plan_name, s.feature) for s in specs)
        if unlocked:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"st:cat:{task_id}:{cat}")])
        else:
            required = min_plan_for(specs[0].feature) if specs else "silver"
            rows.append([InlineKeyboardButton(
                text=f"🔒 {label}",
                callback_data=f"st:lock:{task_id}:{specs[0].key}" if specs else "menu:plans",
            )])

    rows.append([
        InlineKeyboardButton(
            text="▶️ Resume" if task["is_paused"] else "⏸️ Pause",
            callback_data=f"task:{'resume' if task['is_paused'] else 'pause'}:{task_id}",
        ),
        InlineKeyboardButton(text="🗑️ Delete Task", callback_data=f"task:delete:{task_id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="◀️ Back", callback_data="menu:settings"),
        InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
    ])

    text = (
        f"⚙️ <b>Settings for:</b> {safe_html(task['task_name'])}\n"
        f"Status: {status_line}\n"
        f"Plan: <b>{PLANS.get(plan_name, PLANS['free']).name}</b>\n\n"
        f"📥 <b>Sources:</b> {src_names}\n"
        f"📤 <b>Destinations:</b> {dst_names}\n\n"
        f"Choose a category:"
    )
    await _show(callback.message, text, InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


# ==========================================
# CATEGORY SCREEN
# ==========================================

async def _render_category(message_obj, db: Database, user_id: int, task_id: int, cat: str) -> None:
    task, plan_name, language, settings = await _ctx(db, user_id, task_id)
    if task is None:
        return

    if cat == CAT_CHANNELS:
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Sources", callback_data=f"task:edit-source:{task_id}"),
                InlineKeyboardButton(text="📤 Destinations", callback_data=f"task:edit-dest:{task_id}"),
            ],
            [
                InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}"),
                InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
            ],
        ])
        await _show(message_obj, safe_t(language, "settings_cat_channels"), markup)
        return

    title_key = next((k for c, _l, k in CATEGORY_ORDER if c == cat), "settings_cat_messages")
    rows = []
    for spec in SETTING_SPECS.values():
        if spec.category != cat:
            continue
        allowed = plan_has(plan_name, spec.feature)
        if not allowed:
            rows.append([InlineKeyboardButton(
                text=f"🔒 {spec.label}",
                callback_data=f"st:lock:{task_id}:{spec.key}",
            )])
            continue

        # Watermark style/text options are meaningless until the watermark
        # itself is on — showing them enabled would just confuse people.
        if spec.key in ("watermark_text", "wm_position", "wm_size", "wm_opacity"):
            if not settings.get("watermark"):
                continue

        summary = _summary(settings, spec)
        if spec.kind == "toggle":
            current = bool(_current_value(settings, spec))
            rows.append([InlineKeyboardButton(
                text=f"{spec.label} — {summary}",
                callback_data=f"st:tog:{task_id}:{spec.key}:{'0' if current else '1'}",
            )])
        else:
            rows.append([InlineKeyboardButton(
                text=f"{spec.label} — {summary}",
                callback_data=f"st:open:{task_id}:{spec.key}",
            )])

    if cat == CAT_MEDIA and plan_has(plan_name, F_ATTACH_FILE):
        rows.append([InlineKeyboardButton(text="📤 Upload File", callback_data="menu:upload")])

    rows.append([
        InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}"),
        InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
    ])
    await _show(message_obj, safe_t(language, title_key), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("st:cat:"))
async def settings_category_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    _, _, task_id_str, cat = callback.data.split(":")
    task_id = int(task_id_str)
    task, *_ = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)
    await _render_category(callback.message, db, callback.from_user.id, task_id, cat)
    await callback.answer()


@router.callback_query(F.data.startswith("st:lock:"))
async def settings_locked_cb(callback: CallbackQuery, db: Database) -> None:
    if callback.message is None:
        return
    parts = callback.data.split(":")
    task_id, key = int(parts[2]), parts[3]
    spec = SETTING_SPECS.get(key)
    user = await db.get_user(callback.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    required = PLANS.get(min_plan_for(spec.feature), PLANS["platinum"]).name if spec else "Platinum"
    text = safe_t(
        language, "feature_locked",
        feature=spec.display if spec else key, required_plan=required,
    )
    await _show(callback.message, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
        [InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ]))
    await callback.answer()


# ==========================================
# TOGGLES
# ==========================================

@router.callback_query(F.data.startswith("st:tog:"))
async def settings_toggle_cb(
    callback: CallbackQuery, state: FSMContext, db: Database, forwarding: ForwardingEngine,
) -> None:
    _, _, task_id_str, key, val_str = callback.data.split(":")
    task_id = int(task_id_str)
    spec = SETTING_SPECS.get(key)
    if spec is None:
        return await callback.answer("Unknown setting", show_alert=True)

    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)
    if not plan_has(plan_name, spec.feature):
        required = PLANS.get(min_plan_for(spec.feature), PLANS["platinum"]).name
        return await callback.answer(
            safe_t(language, "feature_locked_toast", required_plan=required), show_alert=True,
        )

    new_value = val_str == "1"
    await db.update_task_settings(callback.from_user.id, task_id, {key: new_value})
    await forwarding.refresh_task(task_id)

    if callback.message:
        await _render_category(callback.message, db, callback.from_user.id, task_id, spec.category)
    await callback.answer(
        safe_t(language, "toggle_on" if new_value else "toggle_off", feature=spec.display)
        .replace("<b>", "").replace("</b>", "")
    )

    # Turning the watermark on is useless without text, so ask for it straight away.
    if key == "watermark" and new_value and callback.message:
        await state.set_state(SettingsFlow.waiting_value)
        await state.update_data(task_id=task_id, key="watermark_text", cat=spec.category)
        await callback.message.answer(
            safe_t(language, "watermark_text_prompt"),
            reply_markup=_nav(f"st:cat:{task_id}:{spec.category}"),
            parse_mode="HTML",
        )


# ==========================================
# CHOICE SETTINGS
# ==========================================

@router.callback_query(F.data.startswith("st:choice:"))
async def settings_choice_cb(
    callback: CallbackQuery, db: Database, forwarding: ForwardingEngine,
) -> None:
    _, _, task_id_str, key, value = callback.data.split(":", 4)
    task_id = int(task_id_str)
    spec = SETTING_SPECS.get(key)
    if spec is None or value not in spec.choices:
        return await callback.answer("Invalid option", show_alert=True)

    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)
    if not plan_has(plan_name, spec.feature):
        required = PLANS.get(min_plan_for(spec.feature), PLANS["platinum"]).name
        return await callback.answer(
            safe_t(language, "feature_locked_toast", required_plan=required), show_alert=True,
        )

    if key in ("wm_position", "wm_size", "wm_opacity"):
        style = settings.get("watermark_style")
        style = dict(style) if isinstance(style, dict) else {}
        part = {"wm_position": "position", "wm_size": "size", "wm_opacity": "opacity"}[key]
        style[part] = int(value) if part == "opacity" else value
        await db.update_task_settings(callback.from_user.id, task_id, {"watermark_style": style})
    else:
        await db.update_task_settings(callback.from_user.id, task_id, {key: value})

    await forwarding.refresh_task(task_id)
    if callback.message:
        await _render_category(callback.message, db, callback.from_user.id, task_id, spec.category)
    await callback.answer(safe_t(language, "setting_saved", feature=spec.display)
                          .replace("<b>", "").replace("</b>", ""))


# ==========================================
# AUTO REACTION
# ==========================================

async def _render_reaction(message_obj, db: Database, user_id: int, task_id: int) -> None:
    task, plan_name, language, settings = await _ctx(db, user_id, task_id)
    if task is None:
        return
    cfg = settings.get("auto_reaction")
    cfg = cfg if isinstance(cfg, dict) else {}
    enabled = bool(cfg.get("enabled"))
    emoji = str(cfg.get("emoji") or DEFAULT_REACTION_EMOJI)
    target = str(cfg.get("target") or "source")
    target_label = safe_t(
        language, "reaction_target_source" if target == "source" else "reaction_target_destination"
    )

    rows = [[InlineKeyboardButton(
        text="✅ ON — tap to turn off" if enabled else "❌ OFF — tap to turn on",
        callback_data=f"st:rct:{task_id}:enabled:{'0' if enabled else '1'}",
    )]]

    emoji_row = []
    for item in REACTION_EMOJIS:
        mark = "•" if item == emoji else ""
        emoji_row.append(InlineKeyboardButton(
            text=f"{mark}{item}{mark}", callback_data=f"st:rct:{task_id}:emoji:{item}",
        ))
        if len(emoji_row) == 4:
            rows.append(emoji_row)
            emoji_row = []
    if emoji_row:
        rows.append(emoji_row)

    rows.append([InlineKeyboardButton(
        text=("• " if target == "source" else "") + safe_t(language, "reaction_target_source"),
        callback_data=f"st:rct:{task_id}:target:source",
    )])
    rows.append([InlineKeyboardButton(
        text=("• " if target == "destination" else "") + safe_t(language, "reaction_target_destination"),
        callback_data=f"st:rct:{task_id}:target:destination",
    )])
    rows.append([
        InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}"),
        InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
    ])

    text = safe_t(language, "reaction_prompt", emoji=emoji, target=target_label)
    await _show(message_obj, text, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("st:rct:"))
async def settings_reaction_cb(
    callback: CallbackQuery, db: Database, forwarding: ForwardingEngine,
) -> None:
    _, _, task_id_str, field, value = callback.data.split(":", 4)
    task_id = int(task_id_str)
    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)
    if not plan_has(plan_name, F_AUTO_REACTION):
        required = PLANS.get(min_plan_for(F_AUTO_REACTION), PLANS["platinum"]).name
        return await callback.answer(
            safe_t(language, "feature_locked_toast", required_plan=required), show_alert=True,
        )

    cfg = settings.get("auto_reaction")
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    cfg.setdefault("emoji", DEFAULT_REACTION_EMOJI)
    cfg.setdefault("target", "source")

    if field == "enabled":
        cfg["enabled"] = value == "1"
    elif field == "emoji":
        cfg["emoji"] = value
        # Picking an emoji clearly means "use this", so switch it on too rather
        # than leaving the user wondering why nothing happens.
        cfg["enabled"] = True
    elif field == "target" and value in ("source", "destination"):
        cfg["target"] = value
    else:
        return await callback.answer("Invalid option", show_alert=True)

    await db.update_task_settings(callback.from_user.id, task_id, {"auto_reaction": cfg})
    await forwarding.refresh_task(task_id)
    if callback.message:
        await _render_reaction(callback.message, db, callback.from_user.id, task_id)
    await callback.answer("✅")


# ==========================================
# TOPICS FORWARDING
# ==========================================

async def _render_topics(
    message_obj, db: Database, telethon: TelethonService, user_id: int,
    task_id: int, source_index: int = 0,
) -> None:
    task, plan_name, language, settings = await _ctx(db, user_id, task_id)
    if task is None:
        return

    sources = [s for s in _json_field(task["sources"], []) if isinstance(s, dict)]
    forum_sources = [s for s in sources if s.get("is_forum")]
    back_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}")],
        [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
    ])

    if not forum_sources:
        await _show(message_obj, safe_t(language, "topics_not_forum"), back_markup)
        return

    source_index = max(0, min(source_index, len(forum_sources) - 1))
    source = forum_sources[source_index]
    source_raw = raw_peer_id(source.get("id"))

    # Reading topics goes out to Telegram and can take a few seconds, so show
    # something immediately rather than leaving the screen looking frozen.
    with suppress(Exception):
        await _show(message_obj, "⏳ <b>Loading topics…</b>", None)

    topics = await telethon.get_forum_topics(user_id, source)
    if not topics:
        await _show(message_obj, safe_t(language, "topics_none"), back_markup)
        return

    cfg = settings.get("topics")
    cfg = cfg if isinstance(cfg, dict) else {}
    selected = {int(x) for x in (cfg.get(str(source_raw)) or []) if str(x).lstrip("-").isdigit()}

    rows = []
    for topic in topics[:40]:
        mark = "✅" if topic["id"] in selected else "▫️"
        title = topic["title"][:30]
        rows.append([InlineKeyboardButton(
            text=f"{mark} {title}",
            callback_data=f"st:top:{task_id}:{source_index}:{topic['id']}",
        )])

    if len(forum_sources) > 1:
        nav = []
        if source_index > 0:
            nav.append(InlineKeyboardButton(
                text="⬅️ Prev chat", callback_data=f"st:topnav:{task_id}:{source_index - 1}",
            ))
        if source_index < len(forum_sources) - 1:
            nav.append(InlineKeyboardButton(
                text="Next chat ➡️", callback_data=f"st:topnav:{task_id}:{source_index + 1}",
            ))
        if nav:
            rows.append(nav)

    rows.append([InlineKeyboardButton(
        text="🔄 Clear (forward all topics)", callback_data=f"st:topclr:{task_id}:{source_index}",
    )])
    rows.append([
        InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}"),
        InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
    ])

    header = safe_t(language, "topics_prompt")
    chat_line = f"\n\n📥 <b>{safe_html(source.get('title') or source_raw)}</b>"
    await _show(message_obj, header + chat_line, InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("st:topnav:"))
async def settings_topics_nav_cb(callback: CallbackQuery, db: Database, telethon: TelethonService) -> None:
    if callback.message is None:
        return
    _, _, task_id_str, index_str = callback.data.split(":")
    await _render_topics(
        callback.message, db, telethon, callback.from_user.id, int(task_id_str), int(index_str),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("st:topclr:"))
async def settings_topics_clear_cb(
    callback: CallbackQuery, db: Database, telethon: TelethonService, forwarding: ForwardingEngine,
) -> None:
    if callback.message is None:
        return
    _, _, task_id_str, index_str = callback.data.split(":")
    task_id, source_index = int(task_id_str), int(index_str)
    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)

    sources = [s for s in _json_field(task["sources"], []) if isinstance(s, dict) and s.get("is_forum")]
    if not sources:
        return await callback.answer("Not found", show_alert=True)
    source_raw = raw_peer_id(sources[min(source_index, len(sources) - 1)].get("id"))

    cfg = settings.get("topics")
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    cfg.pop(str(source_raw), None)
    await db.update_task_settings(callback.from_user.id, task_id, {"topics": cfg})
    await forwarding.refresh_task(task_id)
    await _render_topics(callback.message, db, telethon, callback.from_user.id, task_id, source_index)
    await callback.answer(safe_t(language, "topics_cleared").replace("✅ ", ""))


@router.callback_query(F.data.startswith("st:top:"))
async def settings_topics_toggle_cb(
    callback: CallbackQuery, db: Database, telethon: TelethonService, forwarding: ForwardingEngine,
) -> None:
    if callback.message is None:
        return
    _, _, task_id_str, index_str, topic_str = callback.data.split(":")
    task_id, source_index, topic_id = int(task_id_str), int(index_str), int(topic_str)

    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)
    if not plan_has(plan_name, F_TOPICS):
        required = PLANS.get(min_plan_for(F_TOPICS), PLANS["platinum"]).name
        return await callback.answer(
            safe_t(language, "feature_locked_toast", required_plan=required), show_alert=True,
        )

    sources = [s for s in _json_field(task["sources"], []) if isinstance(s, dict) and s.get("is_forum")]
    if not sources:
        return await callback.answer("Not found", show_alert=True)
    source_raw = raw_peer_id(sources[min(source_index, len(sources) - 1)].get("id"))

    cfg = settings.get("topics")
    cfg = dict(cfg) if isinstance(cfg, dict) else {}
    current = [int(x) for x in (cfg.get(str(source_raw)) or []) if str(x).lstrip("-").isdigit()]
    if topic_id in current:
        current.remove(topic_id)
    else:
        current.append(topic_id)
    if current:
        cfg[str(source_raw)] = current
    else:
        cfg.pop(str(source_raw), None)

    await db.update_task_settings(callback.from_user.id, task_id, {"topics": cfg})
    await forwarding.refresh_task(task_id)
    await _render_topics(callback.message, db, telethon, callback.from_user.id, task_id, source_index)
    await callback.answer("✅")


# ==========================================
# OPENING A VALUE EDITOR
# ==========================================

def _format_current(settings: dict, spec: Spec) -> str:
    value = _current_value(settings, spec)
    if spec.kind in ("list", "senders"):
        items = value if isinstance(value, list) else []
        shown = ", ".join(str(x) for x in items) or "—"
    elif spec.kind == "map":
        items = value if isinstance(value, dict) else {}
        shown = ", ".join(f"{k} = {v}" for k, v in list(items.items())[:10]) or "—"
    elif spec.kind == "per_target":
        items = value if isinstance(value, dict) else {}
        shown = ", ".join(items.keys()) or "—"
    elif spec.kind == "number":
        shown = f"{value}s" if value else "Off"
    else:
        shown = str(value) if value else "—"
    return f"\n\n<b>Current:</b> {safe_html(shown)}"


@router.callback_query(F.data.startswith("st:open:"))
async def settings_open_cb(
    callback: CallbackQuery, state: FSMContext, db: Database, telethon: TelethonService,
) -> None:
    if callback.message is None:
        return
    _, _, task_id_str, key = callback.data.split(":")
    task_id = int(task_id_str)
    spec = SETTING_SPECS.get(key)
    if spec is None:
        return await callback.answer("Unknown setting", show_alert=True)

    task, plan_name, language, settings = await _ctx(db, callback.from_user.id, task_id)
    if task is None:
        return await callback.answer("Not found", show_alert=True)
    if not plan_has(plan_name, spec.feature):
        required = PLANS.get(min_plan_for(spec.feature), PLANS["platinum"]).name
        return await callback.answer(
            safe_t(language, "feature_locked_toast", required_plan=required), show_alert=True,
        )

    # Sub-screens that are not plain text input
    if spec.kind == "reaction":
        await _render_reaction(callback.message, db, callback.from_user.id, task_id)
        return await callback.answer()
    if spec.kind == "topics":
        await _render_topics(callback.message, db, telethon, callback.from_user.id, task_id, 0)
        return await callback.answer()

    if spec.kind == "choice":
        current = str(_current_value(settings, spec))
        rows = []
        for option in spec.choices:
            mark = "• " if str(option) == current else ""
            rows.append([InlineKeyboardButton(
                text=f"{mark}{_label_for(option)}",
                callback_data=f"st:choice:{task_id}:{key}:{option}",
            )])
        rows.append([
            InlineKeyboardButton(text="◀️ Back", callback_data=f"st:cat:{task_id}:{spec.category}"),
            InlineKeyboardButton(text="🏠 Home", callback_data="menu:home"),
        ])
        prompt = safe_t(language, spec.prompt) if spec.prompt else spec.display
        await _show(callback.message, prompt, InlineKeyboardMarkup(inline_keyboard=rows))
        return await callback.answer()

    # Free-text input
    await state.set_state(SettingsFlow.waiting_value)
    await state.update_data(task_id=task_id, key=key, cat=spec.category)
    prompt = safe_t(language, spec.prompt) if spec.prompt else "Enter value:"
    await _show(
        callback.message,
        f"{prompt}{_format_current(settings, spec)}",
        _nav(f"st:cat:{task_id}:{spec.category}"),
    )
    await callback.answer()


# ==========================================
# SAVING A TYPED VALUE
# ==========================================

def _parse_value(spec: Spec, text: str) -> tuple[bool, object, str | None]:
    """Parses user input for a spec.

    Returns (ok, value, error_locale_key). Validation failures return the key
    of the message to show rather than raising, so the caller can reply in the
    user's own language.
    """
    if spec.kind == "text":
        return True, text, None

    if spec.kind == "list":
        items = [w.strip() for w in text.split(",") if w.strip()]
        if not items:
            return False, None, "validation_invalid"
        return True, items, None

    if spec.kind == "senders":
        entries = [w.strip() for w in text.split(",") if w.strip()]
        parsed: list = []
        for entry in entries:
            if entry.lstrip("-").isdigit():
                parsed.append(int(entry))
            elif re.fullmatch(r"@[A-Za-z0-9_]{3,64}", entry):
                parsed.append(entry)
            else:
                return False, None, "setting_invalid_ids"
        if not parsed:
            return False, None, "setting_invalid_ids"
        return True, parsed, None

    if spec.kind == "number":
        try:
            number = int(text)
        except ValueError:
            return False, None, "setting_invalid_number"
        if number < 0:
            return False, None, "setting_invalid_number"
        return True, number, None

    if spec.kind == "map":
        mapping: dict[str, str] = {}
        for pair in text.split(","):
            if "=" not in pair:
                continue
            old, new = pair.split("=", 1)
            old_s, new_s = old.strip(), new.strip()
            if old_s and new_s:
                mapping[old_s] = new_s
        if not mapping:
            return False, None, "setting_invalid_replace"
        return True, mapping, None

    if spec.kind == "per_target":
        # One target per line: target=header|footer
        mapping: dict[str, dict] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            target, rest = line.split("=", 1)
            target_key = target.strip().lstrip("@")
            if not target_key:
                continue
            header, _, footer = rest.partition("|")
            mapping[target_key] = {"header": header.strip(), "footer": footer.strip()}
        if not mapping:
            return False, None, "setting_invalid_replace"
        return True, mapping, None

    return False, None, "validation_invalid"


def _empty_for(spec: Spec):
    """The value that represents 'cleared' for each kind."""
    if spec.kind == "text":
        return ""
    if spec.kind in ("list", "senders"):
        return []
    if spec.kind in ("map", "per_target"):
        return {}
    if spec.kind == "number":
        return 0
    return None


@router.message(SettingsFlow.waiting_value)
async def settings_save_value(
    message: Message, state: FSMContext, db: Database, forwarding: ForwardingEngine,
) -> None:
    if not message.text:
        return
    data = await state.get_data()
    task_id = data.get("task_id")
    key = data.get("key")
    cat = data.get("cat")
    spec = SETTING_SPECS.get(key or "")
    if task_id is None or spec is None:
        await state.clear()
        return

    user = await db.get_user(message.from_user.id)
    language = language_for(user["preferred_language"]) if user else "en"
    plan_name = str(user["plan"] or "free") if user else "free"
    raw = message.text.strip()

    if raw.lower() == "/back":
        await state.clear()
        await message.answer(
            safe_t(language, "settings_main_title", task_name="…"),
            reply_markup=_nav(f"st:cat:{task_id}:{cat}"), parse_mode="HTML",
        )
        return

    # Re-check the plan at save time. A subscription can expire between
    # opening the prompt and sending the value.
    if not plan_has(plan_name, spec.feature):
        await state.clear()
        required = PLANS.get(min_plan_for(spec.feature), PLANS["platinum"]).name
        await message.answer(
            safe_t(language, "feature_locked", feature=spec.display, required_plan=required),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 Upgrade Plan", callback_data="menu:plans")],
                [InlineKeyboardButton(text="◀️ Back", callback_data=f"st:task:{task_id}"),
                 InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
            ]),
            parse_mode="HTML",
        )
        return

    cleared = raw.lower() == "/clear"
    if cleared:
        value = _empty_for(spec)
    else:
        ok, value, error_key = _parse_value(spec, raw)
        if not ok:
            await message.answer(
                safe_t(language, error_key or "validation_invalid"),
                reply_markup=_nav(f"st:cat:{task_id}:{cat}"), parse_mode="HTML",
            )
            return

    await db.update_task_settings(message.from_user.id, task_id, {spec.key: value})
    await forwarding.refresh_task(task_id)
    await state.clear()

    await message.answer(
        safe_t(language, "setting_cleared" if cleared else "setting_saved", feature=spec.display),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Back to Settings", callback_data=f"st:cat:{task_id}:{cat}")],
            [InlineKeyboardButton(text="🏠 Home", callback_data="menu:home")],
        ]),
        parse_mode="HTML",
    )
