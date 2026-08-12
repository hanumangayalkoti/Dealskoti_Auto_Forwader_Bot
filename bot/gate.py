from __future__ import annotations

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from .config import Settings
from .db import Database
from .locales import Language, t


def join_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    username = settings.update_channel_username.lstrip("@")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Join Updates Channel", url=f"https://t.me/{username}")],
            [InlineKeyboardButton(text="✅ I've Joined", callback_data="gate:check")],
        ]
    )


async def user_is_member(bot: Bot, settings: Settings, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(settings.update_channel_id, user_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except Exception:
        return False
    return member.status in {"creator", "administrator", "member", "restricted"}


async def enforce_gate(
    bot: Bot,
    db: Database,
    settings: Settings,
    user_id: int,
    language: Language,
    *,
    notify: bool = True,
) -> bool:
    member = await user_is_member(bot, settings, user_id)
    await db.set_membership(user_id, member)
    if member:
        return True
    await db.mark_channel_gate_paused_tasks(user_id)
    if notify:
        await bot.send_message(
            user_id,
            t(language, "join_required"),
            reply_markup=join_keyboard(settings),
        )
    return False