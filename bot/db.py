from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

import asyncpg

from .plans import PLANS

logger = logging.getLogger("dealskoti.db")

MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    plan VARCHAR(50) NOT NULL DEFAULT 'free',
    plan_started_at TIMESTAMPTZ,
    plan_expiry TIMESTAMPTZ,
    preferred_language VARCHAR(20) NOT NULL DEFAULT 'en',
    language_selected BOOLEAN NOT NULL DEFAULT FALSE,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    updates_channel_member BOOLEAN NOT NULL DEFAULT FALSE,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_started_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_payment_id VARCHAR(255);
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT;

CREATE TABLE IF NOT EXISTS sessions (
    user_id BIGINT PRIMARY KEY REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    session_string TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    task_name VARCHAR(255) NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    destinations JSONB NOT NULL DEFAULT '[]'::jsonb,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_paused BOOLEAN NOT NULL DEFAULT FALSE,
    pause_reason VARCHAR(50),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    order_id VARCHAR(255) UNIQUE NOT NULL,
    payment_id VARCHAR(255),
    plan VARCHAR(50) NOT NULL,
    cycle VARCHAR(50) NOT NULL,
    amount_paise INTEGER NOT NULL DEFAULT 0,
    original_amount_paise INTEGER NOT NULL DEFAULT 0,
    discount_amount_paise INTEGER NOT NULL DEFAULT 0,
    payable_amount_paise INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'created',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_daily (
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);

CREATE TABLE IF NOT EXISTS user_files (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(255),
    dummy_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS referrals (
    id SERIAL PRIMARY KEY,
    referrer_id BIGINT NOT NULL REFERENCES users(telegram_user_id),
    referred_id BIGINT NOT NULL REFERENCES users(telegram_user_id),
    commission_amount_paise INTEGER NOT NULL DEFAULT 0,
    is_paid BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (referrer_id, referred_id)
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id SERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    audience VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    total_users INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else [])


class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        async with self.pool.acquire() as conn:
            await conn.execute(MIGRATIONS_SQL)
        logger.info("Database connected and schema verified.")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _require_pool(self) -> asyncpg.Pool:
        if not self.pool:
            raise RuntimeError("Database is not connected")
        return self.pool

    async def ensure_user_with_status(
        self, user_id: int, username: str | None, first_name: str | None,
        referral_code: str | None = None,
    ) -> tuple[asyncpg.Record, bool]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO users (telegram_user_id, username, first_name, referral_code)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (telegram_user_id) DO UPDATE SET
                     username = EXCLUDED.username, first_name = EXCLUDED.first_name,
                     last_seen_at = CURRENT_TIMESTAMP
                   RETURNING *""",
                user_id, username, first_name, referral_code,
            )
            created = row["created_at"] == row["last_seen_at"]
            return row, created

    async def ensure_user(self, user_id: int, username: str | None, first_name: str | None) -> asyncpg.Record:
        row, _ = await self.ensure_user_with_status(user_id, username, first_name)
        return row

    async def get_user(self, user_id: int) -> asyncpg.Record | None:
        async with self._require_pool().acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", user_id)

    async def list_users(self, limit: int = 100, offset: int = 0) -> list[asyncpg.Record]:
        async with self._require_pool().acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM users ORDER BY last_seen_at DESC LIMIT $1 OFFSET $2", limit, offset
            )

    async def count_users(self) -> int:
        async with self._require_pool().acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM users"))

    async def set_language(self, user_id: int, language: str) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                "UPDATE users SET preferred_language=$1, language_selected=TRUE WHERE telegram_user_id=$2",
                language, user_id,
            )

    async def set_membership(self, user_id: int, member: bool) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                "UPDATE users SET updates_channel_member=$1 WHERE telegram_user_id=$2", member, user_id
            )

    async def has_active_session(self, user_id: int) -> bool:
        async with self._require_pool().acquire() as conn:
            return bool(await conn.fetchval("SELECT EXISTS(SELECT 1 FROM sessions WHERE user_id=$1)", user_id))

    async def save_session(self, user_id: int, session_string: str) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO sessions(user_id, session_string) VALUES($1,$2)
                   ON CONFLICT(user_id) DO UPDATE SET session_string=EXCLUDED.session_string,
                   updated_at=CURRENT_TIMESTAMP""",
                user_id, session_string,
            )

    async def get_session(self, user_id: int) -> str | None:
        async with self._require_pool().acquire() as conn:
            return await conn.fetchval("SELECT session_string FROM sessions WHERE user_id=$1", user_id)

    async def delete_session(self, user_id: int) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute("DELETE FROM sessions WHERE user_id=$1", user_id)

    async def list_tasks(self, user_id: int) -> list[asyncpg.Record]:
        async with self._require_pool().acquire() as conn:
            return await conn.fetch("SELECT * FROM tasks WHERE user_id=$1 ORDER BY id", user_id)

    async def get_task(self, task_id: int, user_id: int | None = None) -> asyncpg.Record | None:
        async with self._require_pool().acquire() as conn:
            if user_id is None:
                return await conn.fetchrow("SELECT * FROM tasks WHERE id=$1", task_id)
            return await conn.fetchrow("SELECT * FROM tasks WHERE id=$1 AND user_id=$2", task_id, user_id)

    async def create_task(self, user_id: int, name: str, sources: list[dict], destinations: list[dict],
                          settings: dict | None = None) -> asyncpg.Record:
        async with self._require_pool().acquire() as conn:
            return await conn.fetchrow(
                """INSERT INTO tasks(user_id,task_name,sources,destinations,settings)
                   VALUES($1,$2,$3::jsonb,$4::jsonb,$5::jsonb) RETURNING *""",
                user_id, name, _json(sources), _json(destinations), _json(settings or {}),
            )

    async def update_task(self, task_id: int, user_id: int, **fields: Any) -> asyncpg.Record | None:
        allowed = {"task_name", "sources", "destinations", "settings", "is_paused", "pause_reason"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        if not fields:
            return await self.get_task(task_id, user_id)
        values: list[Any] = []
        assignments: list[str] = []
        for key, value in fields.items():
            if key in {"sources", "destinations", "settings"}:
                assignments.append(f"{key} = ${len(values)+1}::jsonb")
                values.append(_json(value))
            else:
                assignments.append(f"{key} = ${len(values)+1}")
                values.append(value)
        values.extend([task_id, user_id])
        async with self._require_pool().acquire() as conn:
            return await conn.fetchrow(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id=${len(values)-1} AND user_id=${len(values)} RETURNING *",
                *values,
            )

    async def delete_task(self, task_id: int, user_id: int) -> bool:
        async with self._require_pool().acquire() as conn:
            result = await conn.execute("DELETE FROM tasks WHERE id=$1 AND user_id=$2", task_id, user_id)
            return result.endswith("1")

    async def try_reserve_usage(self, user_id: int, daily_limit: int | None) -> bool:
        if daily_limit is None or daily_limit <= 0:
            return True
        async with self._require_pool().acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO usage_daily(user_id, usage_date, message_count)
                   VALUES($1, CURRENT_DATE, 1)
                   ON CONFLICT(user_id, usage_date) DO UPDATE
                   SET message_count=usage_daily.message_count+1
                   WHERE usage_daily.message_count < $2
                   RETURNING message_count""",
                user_id, daily_limit,
            )
            return row is not None

    async def get_usage_today(self, user_id: int) -> int:
        async with self._require_pool().acquire() as conn:
            return int(await conn.fetchval(
                "SELECT COALESCE(message_count,0) FROM usage_daily WHERE user_id=$1 AND usage_date=CURRENT_DATE",
                user_id,
            ) or 0)

    async def create_payment(self, user_id: int, order_id: str, plan: str, cycle: str,
                             original: int, discount: int, payable: int) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                """INSERT INTO payments(user_id,order_id,plan,cycle,amount_paise,
                   original_amount_paise,discount_amount_paise,payable_amount_paise)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                   ON CONFLICT(order_id) DO NOTHING""",
                user_id, order_id, plan, cycle, payable, original, discount, payable,
            )

    async def activate_payment(self, order_id: str, payment_id: str, amount_paise: int) -> asyncpg.Record | None:
        async with self._require_pool().acquire() as conn:
            async with conn.transaction():
                payment = await conn.fetchrow("SELECT * FROM payments WHERE order_id=$1 FOR UPDATE", order_id)
                if not payment or payment["status"] == "captured":
                    return payment
                if payment["payable_amount_paise"] != amount_paise:
                    raise ValueError("Captured amount does not match payment order")
                user = await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id=$1 FOR UPDATE", payment["user_id"])
                now = datetime.now(timezone.utc)
                start = user["plan_expiry"] if user["plan_expiry"] and user["plan_expiry"] > now else now
                expiry = start + timedelta(days={"weekly": 7, "yearly": 365}.get(payment["cycle"], 30))
                await conn.execute(
                    "UPDATE payments SET status='captured', payment_id=$1, amount_paise=$2 WHERE order_id=$3",
                    payment_id, amount_paise, order_id,
                )
                await conn.execute(
                    """UPDATE users SET plan=$1,plan_started_at=COALESCE(plan_started_at,$2),
                       plan_expiry=$3,last_payment_id=$4 WHERE telegram_user_id=$5""",
                    payment["plan"], now, expiry, payment_id, payment["user_id"],
                )
                return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id=$1", payment["user_id"])

    async def get_last_payment(self, user_id: int) -> asyncpg.Record | None:
        async with self._require_pool().acquire() as conn:
            return await conn.fetchrow("SELECT * FROM payments WHERE user_id=$1 ORDER BY created_at DESC LIMIT 1", user_id)

    async def set_plan(self, user_id: int, plan: str, days: int | None = None) -> None:
        if plan not in PLANS:
            raise ValueError("Unknown plan")
        async with self._require_pool().acquire() as conn:
            expiry = None if plan == "free" else datetime.now(timezone.utc) + timedelta(days=days or 30)
            await conn.execute(
                "UPDATE users SET plan=$1,plan_started_at=CASE WHEN $1='free' THEN NULL ELSE CURRENT_TIMESTAMP END,plan_expiry=$2 WHERE telegram_user_id=$3",
                plan, expiry, user_id,
            )

    async def grant_days(self, user_id: int, plan: str, days: int) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute(
                """UPDATE users SET plan=$1, plan_started_at=COALESCE(plan_started_at,CURRENT_TIMESTAMP),
                   plan_expiry=GREATEST(COALESCE(plan_expiry,CURRENT_TIMESTAMP),CURRENT_TIMESTAMP)+($2 * INTERVAL '1 day')
                   WHERE telegram_user_id=$3""",
                plan, days, user_id,
            )

    async def set_blocked(self, user_id: int, blocked: bool) -> None:
        async with self._require_pool().acquire() as conn:
            await conn.execute("UPDATE users SET is_blocked=$1 WHERE telegram_user_id=$2", blocked, user_id)

    async def save_user_file(self, user_id: int, file_id: str, file_name: str, dummy_message_id: int,
                             mime_type: str | None = None) -> int:
        async with self._require_pool().acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM user_files WHERE user_id=$1", user_id)
                return int(await conn.fetchval(
                    "INSERT INTO user_files(user_id,file_id,file_name,mime_type,dummy_message_id) VALUES($1,$2,$3,$4,$5) RETURNING id",
                    user_id, file_id, file_name, mime_type, dummy_message_id,
                ))

    async def list_user_files(self, user_id: int) -> list[asyncpg.Record]:
        async with self._require_pool().acquire() as conn:
            return await conn.fetch("SELECT * FROM user_files WHERE user_id=$1 ORDER BY created_at DESC", user_id)

    async def get_user_file(self, file_id: str) -> asyncpg.Record | None:
        async with self._require_pool().acquire() as conn:
            return await conn.fetchrow("SELECT * FROM user_files WHERE file_id=$1", file_id)
