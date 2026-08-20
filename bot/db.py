import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from .plans import PLANS

logger = logging.getLogger("dealskoti.db")

PLAN_RANKS = {
    "free": 0,
    "silver": 1,
    "gold": 2,
    "platinum": 3,
}

# ---------------------------------------------------------
# SAFE MIGRATIONS: Clean schema with all legacy columns handled
# ---------------------------------------------------------
MIGRATIONS_SQL = """
-- SAFETY: Never drop existing tables on restart. Existing data must survive redeploys.
-- Removed DROP TABLE payments CASCADE which was wiping all payment history every restart.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    telegram_user_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    plan VARCHAR(50) DEFAULT 'free',
    plan_expiry TIMESTAMP WITH TIME ZONE,
    preferred_language VARCHAR(20) DEFAULT 'en',
    language_selected BOOLEAN DEFAULT FALSE,
    is_blocked BOOLEAN DEFAULT FALSE,
    updates_channel_member BOOLEAN DEFAULT FALSE,
    last_seen_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS scheduled_plan VARCHAR(50);
ALTER TABLE users ADD COLUMN IF NOT EXISTS scheduled_days INTEGER;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_new_notified BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    task_name VARCHAR(255) NOT NULL,
    sources JSONB DEFAULT '[]'::jsonb,
    destinations JSONB DEFAULT '[]'::jsonb,
    settings JSONB DEFAULT '{}'::jsonb,
    is_paused BOOLEAN DEFAULT FALSE,
    pause_reason VARCHAR(50),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    user_id BIGINT PRIMARY KEY REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    session_string TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE sessions ADD COLUMN IF NOT EXISTS session_string TEXT;

CREATE TABLE IF NOT EXISTS payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id),
    order_id VARCHAR(255) UNIQUE NOT NULL,
    payment_id VARCHAR(255),
    plan VARCHAR(50) NOT NULL,
    cycle VARCHAR(50) NOT NULL,
    amount_paise INTEGER NOT NULL,
    original_amount_paise INTEGER DEFAULT 0,
    discount_amount_paise INTEGER DEFAULT 0,
    payable_amount_paise INTEGER DEFAULT 0,
    status VARCHAR(50) DEFAULT 'created',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usage_daily (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    UNIQUE (user_id, usage_date)
);

ALTER TABLE usage_daily ADD COLUMN IF NOT EXISTS message_count INTEGER DEFAULT 0;

CREATE TABLE IF NOT EXISTS broadcasts (
    id SERIAL PRIMARY KEY,
    admin_id BIGINT NOT NULL,
    audience VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    total_users INTEGER DEFAULT 0,
    sent INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    blocked INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS referrals (
    id SERIAL PRIMARY KEY,
    referrer_id BIGINT REFERENCES users(telegram_user_id),
    referred_id BIGINT REFERENCES users(telegram_user_id),
    commission_amount_paise INTEGER NOT NULL,
    is_paid BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
"""

class Database:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn)
        if self.pool is None:
            raise RuntimeError("Database pool creation failed")
        async with self.pool.acquire() as conn:
            await conn.execute(MIGRATIONS_SQL)
        logger.info("Database connected and schema verified safely.")

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()

    async def ensure_user(self, user_id: int, username: str | None, first_name: str | None) -> asyncpg.Record:
        user, _ = await self.ensure_user_with_status(user_id, username, first_name)
        return user

    async def ensure_user_with_status(self, user_id: int, username: str | None, first_name: str | None) -> tuple[asyncpg.Record, bool]:
        if self.pool is None: raise RuntimeError("Database not connected")
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", user_id)
            if user:
                await conn.execute(
                    "UPDATE users SET username = $1, first_name = $2, last_seen_at = CURRENT_TIMESTAMP WHERE telegram_user_id = $3",
                    username, first_name, user_id
                )
                return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", user_id), False
            else:
                await conn.execute(
                    "INSERT INTO users (telegram_user_id, username, first_name) VALUES ($1, $2, $3)",
                    user_id, username, first_name
                )
                return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", user_id), True

    async def mark_new_user_notified(self, user_id: int) -> bool:
        if self.pool is None: raise RuntimeError("DB Error")
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT is_new_notified FROM users WHERE telegram_user_id = $1", user_id)
            if val: return False
            await conn.execute("UPDATE users SET is_new_notified = TRUE WHERE telegram_user_id = $1", user_id)
            return True

    async def get_user(self, user_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", user_id)

    async def get_user_by_username(self, username: str) -> asyncpg.Record | None:
        if self.pool is None: return None
        username = username.lstrip("@")
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE username ILIKE $1", username)

    async def list_users(self, limit: int = 15) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM users ORDER BY created_at DESC LIMIT $1", limit)

    async def set_language(self, user_id: int, language: str) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET preferred_language = $1, language_selected = TRUE WHERE telegram_user_id = $2", language, user_id)

    async def set_membership(self, user_id: int, is_member: bool) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET updates_channel_member = $1 WHERE telegram_user_id = $2", is_member, user_id)

    async def set_blocked(self, user_id: int, is_blocked: bool) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("UPDATE users SET is_blocked = $1 WHERE telegram_user_id = $2", is_blocked, user_id)
            return res == "UPDATE 1"

    async def mark_user_inactive(self, user_id: int) -> None:
        await self.set_blocked(user_id, True)

    async def get_users_for_membership_check(self) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT telegram_user_id, updates_channel_member, preferred_language, plan FROM users WHERE is_blocked = FALSE")

    async def has_active_session(self, user_id: int) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1 FROM sessions WHERE user_id = $1 AND session_string IS NOT NULL", user_id)
            return bool(val)

    async def count_tasks(self, user_id: int) -> int:
        if self.pool is None: return 0
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE user_id = $1", user_id)

    async def list_tasks(self, user_id: int) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM tasks WHERE user_id = $1 ORDER BY id ASC", user_id)

    async def get_task(self, task_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM tasks WHERE id = $1", task_id)

    async def create_task_multi(self, user_id: int, name: str, sources: list[dict], dests: list[dict]) -> int:
        if self.pool is None: raise RuntimeError("DB Error")
        async with self.pool.acquire() as conn:
            task_id = await conn.fetchval(
                "INSERT INTO tasks (user_id, task_name, sources, destinations) VALUES ($1, $2, $3, $4) RETURNING id",
                user_id, name, json.dumps(sources), json.dumps(dests)
            )
            return task_id

    async def delete_task(self, user_id: int, task_id: int) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM tasks WHERE id = $1 AND user_id = $2", task_id, user_id)
            return res == "DELETE 1"

    async def set_task_paused(self, user_id: int, task_id: int, paused: bool, reason: str | None) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("UPDATE tasks SET is_paused = $1, pause_reason = $2 WHERE id = $3 AND user_id = $4", paused, reason, task_id, user_id)
            return res == "UPDATE 1"

    async def rename_task(self, user_id: int, task_id: int, new_name: str) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("UPDATE tasks SET task_name = $1 WHERE id = $2 AND user_id = $3", new_name, task_id, user_id)
            return res == "UPDATE 1"

    async def update_task_sources(self, user_id: int, task_id: int, sources: list[dict]) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("UPDATE tasks SET sources = $1 WHERE id = $2 AND user_id = $3", json.dumps(sources), task_id, user_id)
            return res == "UPDATE 1"

    async def update_task_destinations(self, user_id: int, task_id: int, dests: list[dict]) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("UPDATE tasks SET destinations = $1 WHERE id = $2 AND user_id = $3", json.dumps(dests), task_id, user_id)
            return res == "UPDATE 1"

    async def update_task_settings(self, user_id: int, task_id: int, settings_update: dict[str, Any]) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT settings FROM tasks WHERE id = $1 AND user_id = $2", task_id, user_id)
            if not row: return False
            current = json.loads(row["settings"] or "{}")
            current.update(settings_update)
            await conn.execute("UPDATE tasks SET settings = $1 WHERE id = $2", json.dumps(current), task_id)
            return True

    async def mark_channel_gate_paused_tasks(self, user_id: int) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET is_paused = TRUE, pause_reason = 'gate' WHERE user_id = $1 AND is_paused = FALSE", user_id)

    async def resume_channel_gate_tasks(self, user_id: int) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE tasks SET is_paused = FALSE, pause_reason = NULL WHERE user_id = $1 AND pause_reason = 'gate'", user_id)

    async def daily_usage(self, user_id: int) -> int:
        if self.pool is None: return 0
        today = datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT message_count FROM usage_daily WHERE user_id = $1 AND usage_date = $2", user_id, today)
            return val or 0

    async def increment_usage(self, user_id: int) -> None:
        if self.pool is None: return
        today = datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO usage_daily (user_id, usage_date, message_count) 
                VALUES ($1, $2, 1) 
                ON CONFLICT (user_id, usage_date) DO UPDATE SET message_count = usage_daily.message_count + 1
            """, user_id, today)

    async def has_paid_order(self, user_id: int) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1 FROM payments WHERE user_id = $1 AND status = 'captured' LIMIT 1", user_id)
            return bool(val)

    async def save_payment(self, user_id: int, order_id: str, plan: str, cycle: str, original: int, discount: int, payable: int) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO payments (user_id, order_id, plan, cycle, amount_paise, original_amount_paise, discount_amount_paise, payable_amount_paise) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                user_id, order_id, plan, cycle, payable, original, discount, payable
            )

    async def get_payment_for_order(self, order_id: str) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM payments WHERE order_id = $1 AND status != 'captured'", order_id)

    async def activate_payment(self, order_id: str, payment_id: str, amount_paise: int, purchased_days: int, purchased_plan: str, cycle: str) -> int | None:
        if self.pool is None: return None
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                payment = await conn.fetchrow("SELECT user_id, status FROM payments WHERE order_id = $1 FOR UPDATE", order_id)
                if not payment or payment["status"] == "captured":
                    return None  # idempotent: already activated

                user_id = payment["user_id"]
                await conn.execute("UPDATE payments SET payment_id = $1, status = 'captured' WHERE order_id = $2", payment_id, order_id)

                user = await conn.fetchrow("SELECT plan, plan_expiry FROM users WHERE telegram_user_id = $1 FOR UPDATE", user_id)
                if not user: return None

                current_plan = user["plan"] or "free"
                current_expiry = user["plan_expiry"] or now
                if current_expiry < now: current_expiry = now

                current_rank = PLAN_RANKS.get(current_plan, 0)
                purchased_rank = PLAN_RANKS.get(purchased_plan, 0)

                if current_rank == purchased_rank:
                    # SAME-PLAN RENEWAL: extend from current expiry (do not lose remaining time)
                    new_expiry = current_expiry + timedelta(days=purchased_days)
                    await conn.execute(
                        "UPDATE users SET plan_expiry = $1, scheduled_plan = NULL, scheduled_days = NULL WHERE telegram_user_id = $2",
                        new_expiry, user_id,
                    )
                elif purchased_rank < current_rank:
                    # DOWNGRADE: keep higher plan active until expiry, schedule lower plan
                    if current_expiry <= now:
                        # already expired (edge case) - apply immediately
                        new_expiry = now + timedelta(days=purchased_days)
                        await conn.execute(
                            "UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL, scheduled_days = NULL WHERE telegram_user_id = $3",
                            purchased_plan, new_expiry, user_id,
                        )
                    else:
                        await conn.execute(
                            "UPDATE users SET scheduled_plan = $1, scheduled_days = $2 WHERE telegram_user_id = $3",
                            purchased_plan, purchased_days, user_id,
                        )
                else:
                    # UPGRADE: credit unused value as converted higher-plan time
                    target_plan_obj = PLANS.get(purchased_plan)
                    current_plan_obj = PLANS.get(current_plan)
                    target_daily_price = (target_plan_obj.monthly_rupees / 30.0) if target_plan_obj and target_plan_obj.monthly_rupees else 1.0
                    current_daily_price = (current_plan_obj.monthly_rupees / 30.0) if current_plan_obj and current_plan_obj.monthly_rupees else 0.0
                    remaining_days = max(0.0, (current_expiry - now).total_seconds() / 86400.0)
                    remaining_value = current_daily_price * remaining_days
                    converted_days = (remaining_value / target_daily_price) if target_daily_price else 0.0
                    total_new_days = purchased_days + converted_days
                    new_expiry = now + timedelta(days=total_new_days)
                    await conn.execute(
                        "UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL, scheduled_days = NULL WHERE telegram_user_id = $3",
                        purchased_plan, new_expiry, user_id,
                    )
                return user_id

    async def get_expiring_users(self, days: int) -> list[asyncpg.Record]:
        if self.pool is None: return []
        now = datetime.now(timezone.utc)
        target = now + timedelta(days=days)
        start = target - timedelta(hours=1)
        end = target + timedelta(hours=1)
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT telegram_user_id, plan, preferred_language FROM users WHERE plan != 'free' AND plan_expiry BETWEEN $1 AND $2", start, end)

    async def downgrade_expired_users(self) -> list[asyncpg.Record]:
        if self.pool is None: return []
        now = datetime.now(timezone.utc)
        downgraded = []
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                expired = await conn.fetch("SELECT telegram_user_id, scheduled_plan, scheduled_days, preferred_language FROM users WHERE plan != 'free' AND plan_expiry < $1 FOR UPDATE", now)
                for row in expired:
                    uid = row["telegram_user_id"]
                    if row["scheduled_plan"] and row["scheduled_days"]:
                        # Activate scheduled plan (downgrade takes effect at expiry)
                        new_plan = row["scheduled_plan"]
                        new_expiry = now + timedelta(days=row["scheduled_days"])
                        await conn.execute(
                            "UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL, scheduled_days = NULL WHERE telegram_user_id = $3",
                            new_plan, new_expiry, uid,
                        )
                    else:
                        # No scheduled plan -> downgrade to free
                        await conn.execute(
                            "UPDATE users SET plan = 'free', plan_expiry = NULL, scheduled_plan = NULL, scheduled_days = NULL WHERE telegram_user_id = $1",
                            uid,
                        )
                    downgraded.append(row)
        return downgraded

    async def set_plan(self, user_id: int, plan: str, days: int) -> bool:
        if self.pool is None: return False
        if days <= 0:
            return False  # SAFETY: refuse zero/negative days to prevent crash/reset
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT plan_expiry FROM users WHERE telegram_user_id = $1", user_id)
            if not user: return False
            base_time = user["plan_expiry"] if user["plan_expiry"] and user["plan_expiry"] > now else now
            new_expiry = base_time + timedelta(days=days)
            res = await conn.execute("UPDATE users SET plan = $1, plan_expiry = $2 WHERE telegram_user_id = $3", plan, new_expiry, user_id)
            return res == "UPDATE 1"

    async def activate_payment(self, user_id: int, plan: str, cycle: str) -> bool:
        """Convenience wrapper used when admin/manual plans change so that the
        forwarding engine can be hot-reloaded. Returns True on success."""
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                "UPDATE users SET plan = $1 WHERE telegram_user_id = $2",
                plan, user_id
            )
            return res == "UPDATE 1"

    async def stats(self) -> dict[str, Any]:
        if self.pool is None: return {}
        today = datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            users = await conn.fetchval("SELECT COUNT(*) FROM users")
            new_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE DATE(created_at) = $1", today)
            paid = await conn.fetchval("SELECT COUNT(*) FROM users WHERE plan != 'free'")
            active_tasks = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE is_paused = FALSE")
            captured = await conn.fetchval("SELECT COUNT(*) FROM payments WHERE status = 'captured'")
            return {
                "users": users or 0,
                "new_users_today": new_users or 0,
                "paid_users": paid or 0,
                "active_tasks": active_tasks or 0,
                "captured_payments": captured or 0
            }

    async def list_broadcast_users(self, audience: str) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            if audience == "all": return await conn.fetch("SELECT telegram_user_id FROM users WHERE is_blocked = FALSE")
            elif audience == "active": return await conn.fetch("SELECT telegram_user_id FROM users WHERE is_blocked = FALSE AND last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'")
            elif audience == "paid": return await conn.fetch("SELECT telegram_user_id FROM users WHERE plan != 'free' AND is_blocked = FALSE")
            elif audience in ("english", "hinglish"):
                lang = "en" if audience == "english" else "hinglish"
                return await conn.fetch("SELECT telegram_user_id FROM users WHERE preferred_language = $1 AND is_blocked = FALSE", lang)
            return []

    async def create_broadcast(self, admin_id: int, audience: str, message: str, total_users: int) -> int:
        if self.pool is None: raise RuntimeError("DB Error")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "INSERT INTO broadcasts (admin_id, audience, message, total_users) VALUES ($1, $2, $3, $4) RETURNING id",
                admin_id, audience, message, total_users
            )

    async def finish_broadcast(self, broadcast_id: int, sent: int, failed: int, blocked: int) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE broadcasts SET sent = $1, failed = $2, blocked = $3 WHERE id = $4", sent, failed, blocked, broadcast_id)

    async def mark_referral_paid(self, user_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, referrer_id, commission_amount_paise FROM referrals WHERE referred_id = $1 AND is_paid = FALSE LIMIT 1 FOR UPDATE", user_id)
            if row:
                await conn.execute("UPDATE referrals SET is_paid = TRUE WHERE id = $1", row["id"])
                return row
            return None
