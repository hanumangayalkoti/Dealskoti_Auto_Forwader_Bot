import logging
from contextlib import suppress

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from .config import Settings
from .db import Database

logger = logging.getLogger("dealskoti.telethon")

class TelethonService:
    def __init__(self, settings: Settings, db: Database):
        self.api_id = settings.telegram_api_id
        self.api_hash = settings.telegram_api_hash
        self.db = db
        # Stores temporary login states: {user_id: {"client": TelegramClient, "phone": str, "phone_code_hash": str}}
        self.login_clients: dict[int, dict] = {}

    # --- DATABASE SESSION HELPERS ---

    async def _save_session(self, user_id: int, session_string: str) -> None:
        if self.db.pool is None:
            raise RuntimeError("Database not connected")
        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (user_id, session_string) 
                VALUES ($1, $2) 
                ON CONFLICT (user_id) DO UPDATE 
                SET session_string = EXCLUDED.session_string, updated_at = CURRENT_TIMESTAMP
                """,
                user_id, session_string
            )

    async def _delete_session(self, user_id: int) -> None:
        if self.db.pool is None:
            return
        async with self.db.pool.acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)

    async def _get_session_string(self, user_id: int) -> str | None:
        if self.db.pool is None:
            return None
        async with self.db.pool.acquire() as conn:
            return await conn.fetchval("SELECT session_string FROM sessions WHERE user_id = $1", user_id)

    # --- LOGIN FLOW ---

    async def start_phone_login(self, user_id: int, phone: str) -> None:
        await self.cancel_login(user_id)
        
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        await client.connect()
        
        try:
            sent_code = await client.send_code_request(phone)
            self.login_clients[user_id] = {
                "client": client,
                "phone": phone,
                "phone_code_hash": sent_code.phone_code_hash
            }
        except errors.PhoneNumberInvalidError:
            await client.disconnect()
            raise ValueError("The phone number is invalid. Please include the country code (e.g. +91...)")
        except errors.FloodWaitError as e:
            await client.disconnect()
            raise ValueError(f"Telegram blocked requests for {e.seconds} seconds. Try again later.")
        except Exception as e:
            await client.disconnect()
            logger.error(f"Error starting phone login for {user_id}: {e}")
            raise ValueError("Failed to request OTP. Please try again.")

    async def submit_pin(self, user_id: int, pin: str) -> str:
        login_data = self.login_clients.get(user_id)
        if not login_data:
            raise ValueError("Login session expired or not found. Please start over.")

        client: TelegramClient = login_data["client"]
        phone = login_data["phone"]
        phone_code_hash = login_data["phone_code_hash"]

        try:
            await client.sign_in(phone=phone, code=pin, phone_code_hash=phone_code_hash)
            session_string = client.session.save()
            await self._save_session(user_id, session_string)
            # Keep client alive until 2FA step if needed; cleanup happens via
            # submit_2fa() on success or cancel_login() on failure.
            return "success"
        except errors.SessionPasswordNeededError:
            # DON'T cancel login — keep client around so we can submit 2FA next.
            return "2fa_required"
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            # Bad/expired OTP → kill the in-memory client so user must restart.
            await self.cancel_login(user_id)
            raise ValueError("Invalid or expired PIN. Please try again or restart login.")
        except Exception as e:
            logger.error(f"Error submitting PIN for {user_id}: {e}")
            await self.cancel_login(user_id)
            raise ValueError("An unexpected error occurred. Please try again.")

    async def submit_2fa(self, user_id: int, password: str) -> str:
        login_data = self.login_clients.get(user_id)
        if not login_data:
            raise ValueError("Login session expired or not found. Please start over.")

        client: TelegramClient = login_data["client"]

        try:
            await client.sign_in(password=password)
            session_string = client.session.save()
            await self._save_session(user_id, session_string)
            await self.cancel_login(user_id)  # Clean up memory only on success
            return "success"
        except errors.PasswordHashInvalidError:
            # Keep client alive so user can retry the password — DO NOT cancel_login.
            raise ValueError("Incorrect 2FA password. Please try again.")
        except errors.FloodWaitError as e:
            await self.cancel_login(user_id)
            raise ValueError(f"Too many attempts. Try again in {e.seconds} seconds.")
        except Exception as e:
            logger.error(f"Error submitting 2FA for {user_id}: {e}")
            await self.cancel_login(user_id)
            raise ValueError("An unexpected error occurred. Please try again.")

    async def cancel_login(self, user_id: int) -> None:
        login_data = self.login_clients.pop(user_id, None)
        if login_data:
            client: TelegramClient = login_data["client"]
            if client.is_connected():
                await client.disconnect()

    async def cancel_all_logins(self) -> None:
        for uid in list(self.login_clients.keys()):
            await self.cancel_login(uid)

    async def disconnect(self, user_id: int) -> None:
        await self._delete_session(user_id)

    # --- ENTITY VALIDATION (FOR SOURCES / DESTINATIONS) ---

    async def validate_for_user(self, user_id: int, target: str) -> dict:
        """
        Validates if the user's account can access the given target (username or ID).
        Returns a dictionary suitable for storing in JSONB.
        """
        session_string = await self._get_session_string(user_id)
        if not session_string:
            raise ValueError("Your Telegram account is not connected. Please use /connect first.")

        try:
            client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        except (ValueError, TypeError) as exc:
            # Session corrupt — wipe it so the user can /connect again.
            logger.warning(f"Invalid stored session for user {user_id}: {exc}")
            await self.disconnect(user_id)
            raise ValueError("Your stored Telegram session is invalid. Please /connect again.")
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await self.disconnect(user_id)
                raise ValueError("Session expired. Please use /connect to login again.")
                
            # If target looks like a negative or positive ID, treat it as int
            target_clean = target.strip()
            if target_clean.lstrip("-").isdigit():
                target_obj = int(target_clean)
            else:
                target_obj = target_clean

            entity = await client.get_entity(target_obj)
            
            # Extract safe title
            title = getattr(entity, 'title', getattr(entity, 'username', getattr(entity, 'first_name', str(entity.id))))
            
            return {
                "id": entity.id,
                "title": title,
                "access_hash": getattr(entity, 'access_hash', None),
                "type": type(entity).__name__
            }
            
        except ValueError:
            raise ValueError("Could not find this chat. Make sure you are a member of it, or use a valid public username.")
        except errors.FloodWaitError as e:
            raise ValueError(f"Telegram is limiting requests. Try again in {e.seconds} seconds.")
        except Exception as e:
            logger.error(f"Error validating entity '{target}' for user {user_id}: {e}")
            raise ValueError("Failed to validate chat. Please try forwarding a message from it instead.")
        finally:
            if client.is_connected():
                await client.disconnect()

    # --- RECENT CHATS (TOP-20 PICKER) ---

    async def get_top_dialogs(self, user_id: int, limit: int = 20) -> list[dict]:
        """Returns the user's most recent chats/channels (pinned chats come first,
        matching Telegram's own dialog order)."""
        session_string = await self._get_session_string(user_id)
        if not session_string:
            return []
        client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        results: list[dict] = []
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return []
            async for dialog in client.iter_dialogs(limit=limit):
                entity = dialog.entity
                # Skip the bot itself and saved-messages
                if getattr(entity, "bot", False):
                    continue
                results.append({
                    "id": entity.id,
                    "title": dialog.title or getattr(entity, "username", "") or str(entity.id),
                    "access_hash": getattr(entity, "access_hash", None),
                    "username": getattr(entity, "username", None),
                    "type": type(entity).__name__,
                })
        except Exception as e:
            logger.error(f"Error fetching dialogs for user {user_id}: {e}")
        finally:
            if client.is_connected():
                await client.disconnect()
        return results

    # --- BIG FILE DOWNLOAD (MTProto BOT CLIENT, >20MB Bot API limit) ---

    async def download_media_big(self, bot_token: str, chat_id: int, message_id: int, dest_path: str) -> bool:
        """Downloads media from a chat using an MTProto bot client (no 20MB cap)."""
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        try:
            await client.start(bot_token=bot_token)
            msg = await client.get_messages(chat_id, ids=[message_id])
            msg = msg[0] if isinstance(msg, list) else msg
            if not msg or not msg.media:
                return False
            await client.download_media(msg, file=dest_path)
            return True
        except Exception as e:
            logger.error(f"download_media_big failed for chat {chat_id} msg {message_id}: {e}")
            return False
        finally:
            if client.is_connected():
                await client.disconnect()
