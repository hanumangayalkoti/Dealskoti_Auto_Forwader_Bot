from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from .config import Settings
from .db import Database
from .security import SessionCrypto

logger = logging.getLogger("dealskoti.telethon")

class TelethonService:
    def __init__(self, settings: Settings, db: Database):
        self.api_id = settings.telegram_api_id
        self.api_hash = settings.telegram_api_hash
        self.db = db
        self.crypto = SessionCrypto(settings.session_encryption_key)
        
        # Active connected clients for forwarding
        self._clients: dict[int, TelegramClient] = {}
        
        # Temporary clients for users currently in the login flow
        self._login_clients: dict[int, TelegramClient] = {}
        self._phone_hashes: dict[int, str] = {}
        self._phones: dict[int, str] = {}

    async def get_client(self, user_id: int) -> TelegramClient | None:
        """Returns an active, connected client for the user, or creates one from DB."""
        if user_id in self._clients:
            client = self._clients[user_id]
            if not client.is_connected():
                await client.connect()
            return client

        if self.db.pool is None:
            return None

        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT session_string FROM sessions WHERE user_id = $1", user_id)
            
        if not row:
            return None

        stored_value = row["session_string"]
        try:
            session_string = self.crypto.decrypt(stored_value)
        except ValueError:
            # Backward-compat: some rows may still be plaintext from before
            # encryption was wired up. Use as-is, then re-encrypt on write.
            logger.warning("Session for user %s was stored unencrypted; re-encrypting now.", user_id)
            session_string = stored_value
            async with self.db.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE sessions SET session_string = $1 WHERE user_id = $2",
                    self.crypto.encrypt(session_string),
                    user_id,
                )
        client = TelegramClient(StringSession(session_string), self.api_id, self.api_hash)
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.disconnect()
            async with self.db.pool.acquire() as conn:
                await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)
            return None
            
        self._clients[user_id] = client
        return client

    async def start_phone_login(self, user_id: int, phone: str) -> None:
        """Initiates the Telegram login process using a phone number."""
        await self.cancel_login(user_id)
        
        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        await client.connect()
        
        try:
            sent_code = await client.send_code_request(phone)
            self._login_clients[user_id] = client
            self._phone_hashes[user_id] = sent_code.phone_code_hash
            self._phones[user_id] = phone
        except FloodWaitError as e:
            await client.disconnect()
            raise ValueError(f"Telegram rate limit. Try again in {e.seconds} seconds.") from e
        except PhoneNumberInvalidError as e:
            await client.disconnect()
            raise ValueError("Invalid phone number format. Please include country code.") from e
        except Exception as e:
            await client.disconnect()
            logger.exception(f"Error starting phone login for {user_id}")
            raise ValueError("Could not send login code. Ensure number is correct and active.") from e

    async def submit_pin(self, user_id: int, wrapped_code: str) -> str:
        """Submits the Telegram PIN. Code must be wrapped (e.g., 'PIN12345')."""
        if user_id not in self._login_clients:
            raise ValueError("Login session expired. Please start over with /connect.")
            
        client = self._login_clients[user_id]
        phone = self._phones[user_id]
        phone_hash = self._phone_hashes[user_id]
        
        # Clean the wrapped PIN securely (e.g., "PIN12345" -> "12345")
        clean_code = re.sub(r"\D", "", wrapped_code)
        if not clean_code:
            raise ValueError("Invalid PIN format. Send it exactly like PIN12345.")

        try:
            await client.sign_in(phone=phone, code=clean_code, phone_code_hash=phone_hash)
        except SessionPasswordNeededError:
            return "2fa_required"
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as e:
            raise ValueError("Incorrect or expired code. Please try again.") from e
        except FloodWaitError as e:
            raise ValueError(f"Too many attempts. Wait {e.seconds} seconds.") from e
        except Exception as e:
            logger.exception(f"Error submitting PIN for {user_id}")
            raise ValueError("Failed to sign in. Please try /connect again.") from e

        await self._finalize_login(user_id, client)
        return "success"

    async def submit_2fa(self, user_id: int, password: str) -> None:
        """Submits the 2FA password to complete login."""
        if user_id not in self._login_clients:
            raise ValueError("Login session expired. Please start over with /connect.")
            
        client = self._login_clients[user_id]
        
        try:
            await client.sign_in(password=password)
        except PasswordHashInvalidError as e:
            raise ValueError("Incorrect 2FA password. Please try again.") from e
        except FloodWaitError as e:
            raise ValueError(f"Too many attempts. Wait {e.seconds} seconds.") from e
        except Exception as e:
            logger.exception(f"Error submitting 2FA for {user_id}")
            raise ValueError("Failed to sign in. Please try /connect again.") from e

        await self._finalize_login(user_id, client)

    async def _finalize_login(self, user_id: int, client: TelegramClient) -> None:
        """Saves the secure session string to the database and promotes client to active."""
        session_string = client.session.save()
        encrypted_session_string = self.crypto.encrypt(session_string)

        async with self.db.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (user_id, session_string) 
                VALUES ($1, $2) 
                ON CONFLICT (user_id) DO UPDATE 
                SET session_string = EXCLUDED.session_string, updated_at = CURRENT_TIMESTAMP
                """,
                user_id, encrypted_session_string
            )
            
        # Clean up existing active client if any
        if user_id in self._clients:
            old_client = self._clients.pop(user_id)
            await old_client.disconnect()
            
        self._clients[user_id] = client
        
        # Remove from temp dicts
        self._login_clients.pop(user_id, None)
        self._phone_hashes.pop(user_id, None)
        self._phones.pop(user_id, None)

    async def cancel_login(self, user_id: int) -> None:
        """Cancels a pending login and disconnects the temporary client securely."""
        client = self._login_clients.pop(user_id, None)
        self._phone_hashes.pop(user_id, None)
        self._phones.pop(user_id, None)
        
        if client:
            with suppress(Exception):
                await client.disconnect()

    async def cancel_all_logins(self) -> None:
        """Cleans up all temporary and active clients on application shutdown."""
        for client in self._login_clients.values():
            with suppress(Exception):
                await client.disconnect()
        self._login_clients.clear()
        
        for client in self._clients.values():
            with suppress(Exception):
                await client.disconnect()
        self._clients.clear()

    async def disconnect(self, user_id: int) -> None:
        """Logs out the user and removes the session from the database."""
        client = self._clients.pop(user_id, None)
        if client:
            with suppress(Exception):
                await client.log_out()
                
        if self.db.pool:
            async with self.db.pool.acquire() as conn:
                await conn.execute("DELETE FROM sessions WHERE user_id = $1", user_id)

    async def release_client(self, user_id: int) -> None:
        """Disconnects a user's Telethon client WITHOUT deleting their saved
        session — used when they have zero active forwarding tasks, so idle
        connections don't sit open indefinitely and exhaust Railway/Telegram
        connection limits as the user base grows. get_client() will silently
        reconnect from the saved (encrypted) session next time it's needed."""
        client = self._clients.pop(user_id, None)
        if client:
            with suppress(Exception):
                await client.disconnect()

    async def validate_for_user(self, user_id: int, entity_str: str) -> dict:
        """
        Resolves a string/ID into a valid Telegram entity using the user's account.
        Returns a safe dictionary with entity details.
        """
        client = await self.get_client(user_id)
        if not client:
            raise ValueError("You must connect your Telegram account first.")
            
        try:
            # Handle numeric IDs formatted as strings (e.g., "-100123456789")
            if entity_str.lstrip('-').isdigit():
                entity_str_or_int = int(entity_str)
            else:
                entity_str_or_int = entity_str

            entity = await client.get_entity(entity_str_or_int)
            
            title = getattr(entity, "title", None)
            if not title:
                title = getattr(entity, "username", None)
            if not title:
                title = getattr(entity, "first_name", str(entity.id))
                
            return {
                "id": entity.id,
                "title": title,
                "access_hash": getattr(entity, "access_hash", 0)
            }
            
        except Exception as e:
            logger.debug(f"Entity validation failed for {entity_str} (User: {user_id}): {e}")
            raise ValueError("Entity not found. Ensure the username is correct or your connected account is a member of the chat.")

    async def get_recent_chats(self, user_id: int, limit: int = 20) -> list[dict]:
        """
        Fetches up to 20 recent groups/channels to display as numbered inline buttons.
        (Supports the Guided Task Flow UX requirement).
        """
        client = await self.get_client(user_id)
        if not client:
            raise ValueError("You must connect your Telegram account first.")
            
        chats = []
        try:
            dialogs = await client.get_dialogs(limit=limit * 2)  # Fetch more to filter down
            for dialog in dialogs:
                if dialog.is_channel or dialog.is_group:
                    chats.append({
                        "id": dialog.id,
                        "title": dialog.title,
                        "access_hash": getattr(dialog.entity, "access_hash", 0)
                    })
                if len(chats) >= limit:
                    break
        except Exception as e:
            logger.debug(f"Failed to fetch recent chats for User {user_id}: {e}")
            
        return chats
