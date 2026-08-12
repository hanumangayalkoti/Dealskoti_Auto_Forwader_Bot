from __future__ import annotations

import json
from typing import Any

import asyncpg


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_user_id BIGINT PRIMARY KEY,
    username TEXT,
    preferred_language TEXT NOT NULL DEFAULT 'en'
        CHECK (preferred_language IN ('en', 'hinglish')),
    language_selected BOOLEAN NOT NULL DEFAULT FALSE,
    plan TEXT NOT NULL DEFAULT 'free',
    plan_expiry TIMESTAMPTZ,
    trial_started_at TIMESTAMPTZ,
    trial_expires_at TIMESTAMPTZ,
    first_paid_order_at TIMESTAMPTZ,
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    referred_by BIGINT REFERENCES users(telegram_user_id),
    updates_channel_member BOOLEAN NOT NULL DEFAULT FALSE,
    last_membership_check_at TIMESTAMPTZ,
    last_gate_notice_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id BIGINT PRIMARY KEY REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    encrypted_session_string TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_connected_at TIMESTAMPTZ,
    last_error_code TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    task_name TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    destinations JSONB NOT NULL DEFAULT '[]'::jsonb,
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_paused BOOLEAN NOT NULL DEFAULT FALSE,
    pause_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id),
    razorpay_order_id TEXT UNIQUE NOT NULL,
    razorpay_payment_id TEXT UNIQUE,
    plan TEXT NOT NULL,
    cycle TEXT NOT NULL,
    original_amount_paise BIGINT NOT NULL,
    discount_amount_paise BIGINT NOT NULL DEFAULT 0,
    payable_amount_paise BIGINT NOT NULL,
    captured_amount_paise BIGINT,
    status TEXT NOT NULL DEFAULT 'created',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    captured_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS referrals (
    referrer_id BIGINT NOT NULL REFERENCES users(telegram_user_id),
    referred_user_id BIGINT PRIMARY KEY REFERENCES users(telegram_user_id),
    commission_amount_paise BIGINT NOT NULL DEFAULT 0,
    is_paid BOOLEAN NOT NULL DEFAULT FALSE,
    payout_upi_encrypted TEXT,
    requested_at TIMESTAMPTZ,
    paid_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS message_map (
    task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    source_msg_id BIGINT NOT NULL,
    destination_msg_id BIGINT NOT NULL,
    PRIMARY KEY (task_id, source_msg_id, destination_msg_id)
);

CREATE TABLE IF NOT EXISTS usage_daily (
    user_id BIGINT NOT NULL REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    forwarded_messages BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, usage_date)
);

CREATE TABLE IF NOT EXISTS admin_audit_events (
    id BIGSERIAL PRIMARY KEY,
    admin_user_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    target_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.url, min_size=1, max_size=10)
        await self.pool.execute(SCHEMA_SQL)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Database is not connected")
        return self.pool

    async def ensure_user(self, user_id: int, username: str | None) -> asyncpg.Record:
        row = await self._pool().fetchrow(
            """
            INSERT INTO users (telegram_user_id, username)
            VALUES ($1, $2)
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET username = EXCLUDED.username, updated_at = NOW()
            RETURNING *
            """,
            user_id,
            username,
        )
        assert row is not None
        return row

    async def get_user(self, user_id: int) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            "SELECT * FROM users WHERE telegram_user_id = $1", user_id
        )

    async def set_language(self, user_id: int, language: str) -> None:
        await self._pool().execute(
            """
            UPDATE users
            SET preferred_language = $2, language_selected = TRUE, updated_at = NOW()
            WHERE telegram_user_id = $1
            """,
            user_id,
            language,
        )

    async def set_membership(self, user_id: int, is_member: bool) -> None:
        await self._pool().execute(
            """
            UPDATE users
            SET updates_channel_member = $2, last_membership_check_at = NOW(),
                updated_at = NOW()
            WHERE telegram_user_id = $1
            """,
            user_id,
            is_member,
        )

    async def save_session(self, user_id: int, encrypted_session: str) -> None:
        await self._pool().execute(
            """
            INSERT INTO sessions (user_id, encrypted_session_string, is_active,
                                  last_connected_at)
            VALUES ($1, $2, TRUE, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                encrypted_session_string = EXCLUDED.encrypted_session_string,
                is_active = TRUE, last_connected_at = NOW(),
                last_error_code = NULL, updated_at = NOW()
            """,
            user_id,
            encrypted_session,
        )

    async def deactivate_session(self, user_id: int) -> None:
        await self._pool().execute(
            "UPDATE sessions SET is_active = FALSE, updated_at = NOW() WHERE user_id = $1",
            user_id,
        )

    async def has_active_session(self, user_id: int) -> bool:
        value = await self._pool().fetchval(
            "SELECT EXISTS (SELECT 1 FROM sessions WHERE user_id = $1 AND is_active = TRUE)",
            user_id,
        )
        return bool(value)

    async def get_active_sessions(self) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            """
            SELECT u.telegram_user_id, u.plan, u.is_blocked,
                   u.updates_channel_member, s.encrypted_session_string
            FROM users u
            JOIN sessions s ON s.user_id = u.telegram_user_id
            WHERE s.is_active = TRUE AND u.is_blocked = FALSE
            """
        )

    async def create_task(
        self,
        user_id: int,
        task_name: str,
        source: dict[str, Any],
        destination: dict[str, Any],
    ) -> int:
        row = await self._pool().fetchrow(
            """
            INSERT INTO tasks (user_id, task_name, sources, destinations)
            VALUES ($1, $2, $3::jsonb, $4::jsonb)
            RETURNING id
            """,
            user_id,
            task_name,
            json.dumps([source]),
            json.dumps([destination]),
        )
        assert row is not None
        return int(row["id"])

    async def list_tasks(self, user_id: int) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            "SELECT * FROM tasks WHERE user_id = $1 ORDER BY id", user_id
        )

    async def get_active_tasks(self) -> list[asyncpg.Record]:
        return await self._pool().fetch(
            """
            SELECT id, user_id, task_name, sources, destinations, settings
            FROM tasks
            WHERE is_paused = FALSE
            ORDER BY user_id, id
            """
        )

    async def set_task_paused(
        self, user_id: int, task_id: int, paused: bool, reason: str | None = None
    ) -> bool:
        result = await self._pool().execute(
            """
            UPDATE tasks
            SET is_paused = $3, pause_reason = $4, updated_at = NOW()
            WHERE user_id = $1 AND id = $2
            """,
            user_id,
            task_id,
            paused,
            reason,
        )
        return result.endswith("1")

    async def delete_task(self, user_id: int, task_id: int) -> bool:
        result = await self._pool().execute(
            "DELETE FROM tasks WHERE user_id = $1 AND id = $2", user_id, task_id
        )
        return result.endswith("1")

    async def count_tasks(self, user_id: int) -> int:
        value = await self._pool().fetchval(
            "SELECT COUNT(*) FROM tasks WHERE user_id = $1 AND is_paused = FALSE",
            user_id,
        )
        return int(value or 0)

    async def save_payment(
        self,
        user_id: int,
        order_id: str,
        plan: str,
        cycle: str,
        original: int,
        discount: int,
        payable: int,
    ) -> None:
        await self._pool().execute(
            """
            INSERT INTO payments
                (user_id, razorpay_order_id, plan, cycle, original_amount_paise,
                 discount_amount_paise, payable_amount_paise)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (razorpay_order_id) DO NOTHING
            """,
            user_id,
            order_id,
            plan,
            cycle,
            original,
            discount,
            payable,
        )

    async def has_paid_order(self, user_id: int) -> bool:
        value = await self._pool().fetchval(
            "SELECT EXISTS (SELECT 1 FROM payments WHERE user_id = $1 AND status = 'captured')",
            user_id,
        )
        return bool(value)

    async def get_payment_for_order(self, order_id: str) -> asyncpg.Record | None:
        return await self._pool().fetchrow(
            "SELECT * FROM payments WHERE razorpay_order_id = $1", order_id
        )

    async def activate_payment(
        self,
        order_id: str,
        payment_id: str,
        captured_amount: int,
        duration_days: int,
        plan: str,
        cycle: str,
    ) -> int | None:
        async with self._pool().acquire() as connection:
            async with connection.transaction():
                payment = await connection.fetchrow(
                    "SELECT * FROM payments WHERE razorpay_order_id = $1 FOR UPDATE",
                    order_id,
                )
                if payment is None:
                    raise ValueError("Razorpay order was not created by this service")
                if payment["status"] == "captured":
                    return None
                if int(payment["payable_amount_paise"]) != captured_amount:
                    raise ValueError("Captured payment amount does not match the stored order")
                if payment["plan"] != plan:
                    raise ValueError("Captured payment plan does not match the stored order")
                if payment["cycle"] != cycle:
                    raise ValueError("Captured payment cycle does not match the stored order")
                user_id = int(payment["user_id"])
                await connection.execute(
                    """
                    UPDATE payments
                    SET razorpay_payment_id = $2, captured_amount_paise = $3,
                        status = 'captured', captured_at = NOW()
                    WHERE razorpay_order_id = $1
                    """,
                    order_id,
                    payment_id,
                    captured_amount,
                )
                await connection.execute(
                    """
                    UPDATE users
                    SET plan = $2,
                        plan_expiry = GREATEST(COALESCE(plan_expiry, NOW()), NOW())
                                     + ($3 || ' days')::interval,
                        first_paid_order_at = COALESCE(first_paid_order_at, NOW()),
                        updated_at = NOW()
                    WHERE telegram_user_id = $1
                    """,
                    user_id,
                    plan,
                    duration_days,
                )
                return user_id

    async def mark_channel_gate_paused_tasks(self, user_id: int) -> None:
        await self._pool().execute(
            """
            UPDATE tasks
            SET is_paused = TRUE, pause_reason = 'updates_channel_gate', updated_at = NOW()
            WHERE user_id = $1 AND is_paused = FALSE
            """,
            user_id,
        )

    async def resume_channel_gate_tasks(self, user_id: int) -> None:
        await self._pool().execute(
            """
            UPDATE tasks
            SET is_paused = FALSE, pause_reason = NULL, updated_at = NOW()
            WHERE user_id = $1 AND pause_reason = 'updates_channel_gate'
            """,
            user_id,
        )

    async def stats(self) -> dict[str, int]:
        row = await self._pool().fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM users) AS users,
                (SELECT COUNT(*) FROM users WHERE plan <> 'free') AS paid_users,
                (SELECT COUNT(*) FROM tasks WHERE is_paused = FALSE) AS active_tasks,
                (SELECT COUNT(*) FROM payments WHERE status = 'captured') AS captured_payments
            """
        )
        assert row is not None
        return {key: int(row[key] or 0) for key in row.keys()}