import logging
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Settings
from .db import Database

logger = logging.getLogger("dealskoti.gate")

async def user_is_member(bot: Bot, settings: Settings, user_id: int) -> bool:
    """Checks if the user is a member of the mandatory update channel."""
    if not settings.update_channel_username:
        return True
        
    try:
        member = await bot.get_chat_member(settings.update_channel_username, user_id)
        # allowed statuses for a valid member
        return member.status in ["member", "administrator", "creator"]
    except (TelegramBadRequest, TelegramForbiddenError):
        # Either the bot is not an admin in the channel, or the user hasn't started the bot/left
        return False
    except Exception as e:
        logger.error(f"Error checking channel membership for {user_id}: {e}")
        return False

def join_keyboard(settings: Settings, language: str) -> InlineKeyboardMarkup:
    """Returns the inline keyboard forcing the user to join."""
    channel_url = f"https://t.me/{settings.update_channel_username.lstrip('@')}"
    btn_join = "📢 Join Updates Channel" if language == "en" else "📢 Updates Channel Join Karein"
    btn_check = "✅ I've Joined" if language == "en" else "✅ Maine Join Kar Liya"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=btn_join, url=channel_url)],
            [InlineKeyboardButton(text=btn_check, callback_data="gate:check")]
        ]
    )

async def enforce_gate(bot: Bot, db: Database, settings: Settings, user_id: int, language: str) -> bool:
    """
    Enforces the channel gate. Returns True if the user is allowed to proceed.
    If False, it sends the user a prompt to join the channel.
    """
    if not settings.update_channel_username:
        return True
        
    is_member = await user_is_member(bot, settings, user_id)
    
    # Keep the database in sync for background forwarding tasks
    await db.set_membership(user_id, is_member)
    
    if not is_member:
        text = (
            "⚠️ **Action Required**\n\nYou must join our Updates Channel to use this bot and keep your forwarding active." 
            if language == "en" else 
            "⚠️ **Dhyan Dein**\n\nBot ko use karne aur apna forwarding chalu rakhne ke liye humara Updates Channel join karna zaroori hai."
        )
        with suppress(Exception):
            await bot.send_message(user_id, text, reply_markup=join_keyboard(settings, language), parse_mode="HTML")
        return False
        
    return True
