import asyncio
import logging
from contextlib import suppress

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from .config import Settings
from .db import Database

logger = logging.getLogger("dealskoti.telethon")

class TelethonService:
    LOGIN_TIMEOUT_SECONDS = 10 * 60
    MAX_PIN_ATTEMPTS = 5
    MAX_2FA_ATTEMPTS = 5

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
                "phone_code_hash": sent_code.phone_code_hash,
                "pin_attempts": 0,
                "two_fa_attempts": 0,
            }
            self.login_clients[user_id]["expiry_task"] = asyncio.create_task(
                self._expire_login(user_id, client)
            )
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
            login_data["pin_attempts"] += 1
            if login_data["pin_attempts"] > self.MAX_PIN_ATTEMPTS:
                await self.cancel_login(user_id)
                raise ValueError("Too many PIN attempts. Please start login again.")

            await client.sign_in(phone=phone, code=pin, phone_code_hash=phone_code_hash)
            session_string = client.session.save()
            await self._save_session(user_id, session_string)
            await self.cancel_login(user_id)
            return "success"
        except errors.SessionPasswordNeededError:
            # DON'T cancel login — keep client around so we can submit 2FA next.
            return "2fa_required"
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            if login_data["pin_attempts"] >= self.MAX_PIN_ATTEMPTS:
                await self.cancel_login(user_id)
                raise ValueError("Too many PIN attempts. Please start login again.")
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
            login_data["two_fa_attempts"] += 1
            if login_data["two_fa_attempts"] > self.MAX_2FA_ATTEMPTS:
                await self.cancel_login(user_id)
                raise ValueError("Too many 2FA attempts. Please start login again.")

            await client.sign_in(password=password)
            session_string = client.session.save()
            await self._save_session(user_id, session_string)
            await self.cancel_login(user_id)  # Clean up memory only on success
            return "success"
        except errors.PasswordHashInvalidError:
            if login_data["two_fa_attempts"] >= self.MAX_2FA_ATTEMPTS:
                await self.cancel_login(user_id)
                raise ValueError("Too many 2FA attempts. Please start login again.")
            raise ValueError("Incorrect 2FA password. Please try again.")
        except errors.FloodWaitError as e:
            await self.cancel_login(user_id)
            raise ValueError(f"Too many attempts. Try again in {e.seconds} seconds.")
        except Exception as e:
            logger.error(f"Error submitting 2FA for {user_id}: {e}")
            await self.cancel_login(user_id)
            raise ValueError("An unexpected error occurred. Please try again.")

    async def _expire_login(self, user_id: int, client: TelegramClient) -> None:
        await asyncio.sleep(self.LOGIN_TIMEOUT_SECONDS)
        login_data = self.login_clients.get(user_id)
        if login_data and login_data.get("client") is client:
            logger.info("Expiring inactive Telegram login for user %s", user_id)
            await self.cancel_login(user_id)

    async def cancel_login(self, user_id: int) -> None:
        login_data = self.login_clients.pop(user_id, None)
        if login_data:
            expiry_task = login_data.get("expiry_task")
            if expiry_task and expiry_task is not asyncio.current_task():
                expiry_task.cancel()
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
