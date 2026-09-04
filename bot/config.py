from __future__ import annotations

import os
from dataclasses import dataclass

# Try to load from .env file if dotenv is installed (useful for local testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class ConfigurationError(Exception):
    """Raised when a required environment variable is missing or invalid."""


@dataclass
class Settings:
    telegram_bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    database_url: str
    admin_telegram_ids: list[int]
    update_channel_username: str

    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str
    razorpay_webhook_path: str

    support_bot_link: str | None = None
    log_level: str = "INFO"
    default_timezone: str = "Asia/Kolkata"
    max_concurrent_forward_tasks: int = 100

    # USDT manual payments (optional — if wallet empty, the USDT option is hidden)
    usdt_wallet_address: str = ""
    usdt_network: str = "TRC20"

    # Telegram Stars manual payments (optional — if empty, the Stars option is
    # hidden). This is the @username or display name users send Stars to; it is
    # shown verbatim in the payment instructions.
    stars_receiver: str = ""

    # Private channel the nightly database backup is sent to. Leave unset to
    # disable backups entirely.
    backup_channel_id: int | None = None

    # Platinum file-upload storage
    file_storage_channel_id: int | None = None
    max_file_size_mb: int = 2000

    # Optional Fernet key used to encrypt Telethon session strings at rest.
    # When empty, sessions are stored as-is (backwards compatible).
    session_encryption_key: str = ""

    @property
    def stars_enabled(self) -> bool:
        return bool(self.stars_receiver)

    @property
    def usdt_enabled(self) -> bool:
        return bool(self.usdt_wallet_address)

    @classmethod
    def from_env(cls) -> Settings:
        """Loads and validates all configuration from environment variables."""

        def get_env(key: str, default: str | None = None, required: bool = True) -> str:
            val = os.getenv(key, default)
            if required and not val:
                raise ConfigurationError(f"Missing required environment variable: {key}")
            return val.strip() if val else ""

        # --- TELEGRAM BOT & API ---
        telegram_bot_token = get_env("TELEGRAM_BOT_TOKEN")

        try:
            telegram_api_id = int(get_env("TELEGRAM_API_ID"))
        except ValueError:
            raise ConfigurationError("TELEGRAM_API_ID must be a valid integer.")

        telegram_api_hash = get_env("TELEGRAM_API_HASH")

        # --- DATABASE ---
        database_url = get_env("DATABASE_URL")

        # --- ADMINS ---
        admin_ids_raw = get_env("ADMIN_TELEGRAM_IDS")
        try:
            admin_telegram_ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]
        except ValueError:
            raise ConfigurationError("ADMIN_TELEGRAM_IDS must be a comma-separated list of integers.")

        if not admin_telegram_ids:
            raise ConfigurationError("At least one admin ID must be provided in ADMIN_TELEGRAM_IDS.")

        # --- CHANNELS & LINKS ---
        update_channel_username = get_env("UPDATE_CHANNEL_USERNAME")
        if not update_channel_username.startswith("@"):
            update_channel_username = f"@{update_channel_username}"

        support_bot_link = get_env("SUPPORT_BOT_LINK", required=False) or None

        # --- RAZORPAY BILLING ---
        razorpay_key_id = get_env("RAZORPAY_KEY_ID")
        razorpay_key_secret = get_env("RAZORPAY_KEY_SECRET")
        razorpay_webhook_secret = get_env("RAZORPAY_WEBHOOK_SECRET")
        razorpay_webhook_path = get_env("RAZORPAY_WEBHOOK_PATH", default="/webhooks/razorpay", required=False)
        if not razorpay_webhook_path.startswith("/"):
            razorpay_webhook_path = f"/{razorpay_webhook_path}"

        # --- MISC & OPTIONAL ---
        log_level = get_env("LOG_LEVEL", default="INFO", required=False).upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = "INFO"

        default_timezone = get_env("DEFAULT_TIMEZONE", default="Asia/Kolkata", required=False)

        try:
            max_tasks = int(get_env("MAX_CONCURRENT_FORWARD_TASKS", default="100", required=False))
        except ValueError:
            max_tasks = 100

        # --- USDT MANUAL PAYMENTS (OPTIONAL) ---
        usdt_wallet_address = get_env("USDT_WALLET_ADDRESS", required=False) or ""
        usdt_network = get_env("USDT_NETWORK", default="TRC20", required=False) or "TRC20"

        # --- TELEGRAM STARS MANUAL PAYMENTS (OPTIONAL) ---
        # Set STARS_RECEIVER to the @username Stars should be sent to. While it
        # is empty the "Pay with Telegram Stars" button is hidden entirely, so
        # nobody can start a payment that has nowhere to go.
        stars_receiver = get_env("STARS_RECEIVER", required=False) or ""
        if stars_receiver and not stars_receiver.startswith(("@", "http")):
            stars_receiver = f"@{stars_receiver}"

        # --- NIGHTLY DATABASE BACKUP (OPTIONAL BUT STRONGLY RECOMMENDED) ---
        # A PRIVATE channel where the bot is an admin. Never put the
        # SESSION_ENCRYPTION_KEY in the same place: sessions in the dump are
        # encrypted, and keeping the key apart is what keeps them safe if the
        # backup ever leaks.
        backup_channel_id: int | None = None
        bk_raw = get_env("BACKUP_CHANNEL_ID", required=False) or ""
        if bk_raw.lstrip("-").isdigit():
            backup_channel_id = int(bk_raw)

        # --- FILE STORAGE (PLATINUM /upload_file) ---
        file_storage_channel_id: int | None = None
        fs_raw = get_env("FILE_STORAGE_CHANNEL_ID", required=False) or ""
        if fs_raw.lstrip("-").isdigit():
            file_storage_channel_id = int(fs_raw)
        try:
            max_file_size_mb = int(get_env("MAX_FILE_SIZE_MB", default="2000", required=False))
        except ValueError:
            max_file_size_mb = 2000

        # --- SESSION ENCRYPTION (OPTIONAL BUT RECOMMENDED) ---
        session_encryption_key = get_env("SESSION_ENCRYPTION_KEY", required=False) or ""

        return cls(
            telegram_bot_token=telegram_bot_token,
            telegram_api_id=telegram_api_id,
            telegram_api_hash=telegram_api_hash,
            database_url=database_url,
            admin_telegram_ids=admin_telegram_ids,
            update_channel_username=update_channel_username,
            razorpay_key_id=razorpay_key_id,
            razorpay_key_secret=razorpay_key_secret,
            razorpay_webhook_secret=razorpay_webhook_secret,
            razorpay_webhook_path=razorpay_webhook_path,
            support_bot_link=support_bot_link,
            log_level=log_level,
            default_timezone=default_timezone,
            max_concurrent_forward_tasks=max_tasks,
            backup_channel_id=backup_channel_id,
            usdt_wallet_address=usdt_wallet_address,
            usdt_network=usdt_network,
            stars_receiver=stars_receiver,
            file_storage_channel_id=file_storage_channel_id,
            max_file_size_mb=max_file_size_mb,
            session_encryption_key=session_encryption_key,
        )
