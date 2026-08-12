from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(RuntimeError):
    """Raised when a required runtime setting is missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable is missing: {name}")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    database_url: str
    session_encryption_key: str
    admin_telegram_ids: frozenset[int]
    update_channel_username: str
    update_channel_id: int
    support_bot_link: str
    default_timezone: str
    log_level: str
    max_image_size_mb: int
    max_concurrent_forward_tasks: int
    razorpay_key_id: str | None
    razorpay_key_secret: str | None
    razorpay_webhook_secret: str | None
    razorpay_webhook_path: str
    public_base_url: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        raw_admin_ids = os.getenv("ADMIN_TELEGRAM_IDS", "").strip()
        admin_ids: set[int] = set()
        if raw_admin_ids:
            for raw_id in raw_admin_ids.split(","):
                try:
                    admin_ids.add(int(raw_id.strip()))
                except ValueError as exc:
                    raise ConfigurationError(
                        "ADMIN_TELEGRAM_IDS must contain comma-separated numeric IDs"
                    ) from exc

        try:
            api_id = int(_required("TELEGRAM_API_ID"))
            channel_id = int(_required("UPDATE_CHANNEL_ID"))
        except ValueError as exc:
            raise ConfigurationError(
                "TELEGRAM_API_ID and UPDATE_CHANNEL_ID must be numeric"
            ) from exc

        channel_username = _required("UPDATE_CHANNEL_USERNAME")
        if not channel_username.startswith("@"):
            raise ConfigurationError("UPDATE_CHANNEL_USERNAME must start with @")

        return cls(
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            telegram_api_id=api_id,
            telegram_api_hash=_required("TELEGRAM_API_HASH"),
            database_url=_required("DATABASE_URL"),
            session_encryption_key=_required("SESSION_ENCRYPTION_KEY"),
            admin_telegram_ids=frozenset(admin_ids),
            update_channel_username=channel_username,
            update_channel_id=channel_id,
            support_bot_link=os.getenv("SUPPORT_BOT_LINK", "").strip(),
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Kolkata"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            max_image_size_mb=_positive_int("MAX_IMAGE_SIZE_MB", 20),
            max_concurrent_forward_tasks=_positive_int(
                "MAX_CONCURRENT_FORWARD_TASKS", 10
            ),
            razorpay_key_id=os.getenv("RAZORPAY_KEY_ID", "").strip() or None,
            razorpay_key_secret=os.getenv("RAZORPAY_KEY_SECRET", "").strip() or None,
            razorpay_webhook_secret=os.getenv(
                "RAZORPAY_WEBHOOK_SECRET", ""
            ).strip()
            or None,
            razorpay_webhook_path=os.getenv(
                "RAZORPAY_WEBHOOK_PATH", "/webhooks/razorpay"
            ).strip()
            or "/webhooks/razorpay",
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
            or None,
        )