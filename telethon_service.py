from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

from .config import Settings
from .db import Database
from .security import SessionCrypto


@dataclass
class LoginAttempt:
    client: TelegramClient
    phone: str
    phone_code_hash: str
    created_at: float


class TelethonService:
    """Short-lived login state plus encrypted persistent sessions."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.crypto = SessionCrypto(settings.session_encryption_key)
        self._attempts: dict[int, LoginAttempt] = {}
        self._lock = asyncio.Lock()

    async def start_phone_login(self, user_id: int, phone: str) -> None:
        if not re.fullmatch(r"\+[1-9]\d{7,14}", phone.strip()):
            raise ValueError("Phone number must include a valid country code.")
        async with self._lock:
            await self.cancel_login(user_id)
            client = TelegramClient(
                StringSession(),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
            )
            await client.connect()
            sent = await client.send_code_request(phone.strip())
            self._attempts[user_id] = LoginAttempt(
                client, phone.strip(), sent.phone_code_hash, time.monotonic()
            )

    def _attempt(self, user_id: int) -> LoginAttempt:
        attempt = self._attempts.get(user_id)
        if attempt is None or time.monotonic() - attempt.created_at > 600:
            raise ValueError("Login session expired. Use /connect again.")
        return attempt

    async def submit_pin(self, user_id: int, wrapped_pin: str) -> str:
        match = re.fullmatch(r"PIN\s*(\d{3,8})", wrapped_pin.strip(), flags=re.IGNORECASE)
        if not match:
            raise ValueError("Send the code only in PIN123 format.")
        attempt = self._attempt(user_id)
        try:
            await attempt.client.sign_in(
                phone=attempt.phone,
                code=match.group(1),
                phone_code_hash=attempt.phone_code_hash,
            )
        except SessionPasswordNeededError:
            return "2fa_required"
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
            raise ValueError("Telegram code is invalid or expired.") from exc
        await self._persist_success(user_id, attempt)
        return "connected"

    async def submit_2fa(self, user_id: int, password: str) -> None:
        if not password:
            raise ValueError("2FA password cannot be empty.")
        attempt = self._attempt(user_id)
        try:
            await attempt.client.sign_in(password=password)
        except Exception as exc:
            if exc.__class__.__name__ == "PasswordHashInvalidError":
                raise ValueError("2FA password is incorrect.") from exc
            raise ValueError("Telegram could not verify the 2FA password.") from exc
        await self._persist_success(user_id, attempt)

    async def _persist_success(self, user_id: int, attempt: LoginAttempt) -> None:
        session_string = attempt.client.session.save()
        await self.db.save_session(user_id, self.crypto.encrypt(session_string))
        self._attempts.pop(user_id, None)
        await attempt.client.disconnect()

    async def cancel_login(self, user_id: int) -> None:
        attempt = self._attempts.pop(user_id, None)
        if attempt is not None:
            await attempt.client.disconnect()

    async def disconnect(self, user_id: int) -> None:
        await self.cancel_login(user_id)
        await self.db.deactivate_session(user_id)

    async def validate_for_user(
        self, user_id: int, value: str
    ) -> dict[str, str | int]:
        rows = await self.db.get_active_sessions()
        session = next(
            (row for row in rows if int(row["telegram_user_id"]) == user_id), None
        )
        if session is None:
            raise ValueError("Connect your Telegram account first with /connect.")
        client = await self.open_saved_client(session["encrypted_session_string"])
        try:
            return await self.validate_entity(client, value)
        finally:
            await client.disconnect()

    async def open_saved_client(self, encrypted_session: str) -> TelegramClient:
        session = self.crypto.decrypt(encrypted_session)
        client = TelegramClient(
            StringSession(session),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        )
        await client.connect()
        return client

    async def validate_entity(self, client: TelegramClient, value: str) -> dict[str, str | int]:
        candidate = int(value) if re.fullmatch(r"-?\d+", value.strip()) else value.strip()
        try:
            entity = await client.get_entity(candidate)
        except Exception as exc:
            raise ValueError("Telegram entity could not be resolved by this account.") from exc
        entity_id = int(getattr(entity, "id", 0))
        if not entity_id:
            raise ValueError("Telegram entity did not have a valid ID.")
        return {"chat_id": entity_id, "label": value.strip()}

    async def cancel_all_logins(self) -> None:
        for user_id in list(self._attempts):
            await self.cancel_login(user_id)