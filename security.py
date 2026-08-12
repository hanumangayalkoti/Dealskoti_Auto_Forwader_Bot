from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SessionCrypto:
    """Encrypt Telethon session strings before they are written to PostgreSQL."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "SESSION_ENCRYPTION_KEY must be a valid Fernet key"
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Encrypted Telegram session could not be decrypted") from exc