"""
PostgreSQL layer for the DealsKoti forwarder bot.

NON-NEGOTIABLE RULES (do not "optimise" these away in a rewrite):
  1. NEVER add a DROP TABLE to MIGRATIONS_SQL. A redeploy must never wipe
     production data. All schema changes are additive and idempotent.
  2. Every query is parameterised ($1, $2 ...). Never build SQL with f-strings.
  3. Money-touching writes (activate_payment) run inside a transaction with
     `FOR UPDATE` row locks and must stay idempotent — a webhook can and does
     fire twice for the same payment.
"""

import json
import logging
import os
import secrets
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from .plans import PLANS, referral_commission_paise

logger = logging.getLogger("dealskoti.db")

PLAN_RANKS = {
    "free": 0,
    "basic": 1,
    "silver": 2,
    "gold": 3,
    "platinum": 4,
}

# ---------------------------------------------------------
# SAFE MIGRATIONS — additive only, safe to run on every boot
# ---------------------------------------------------------
MIGRATIONS_SQL = """
-- SAFETY: Never drop existing tables on restart. Existing data must survive redeploys.

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
ALTER TABLE users ADD COLUMN IF NOT EXISTS task_reminder_sent BOOLEAN DEFAULT FALSE;
-- Existing users must not receive the new-user onboarding reminder. New rows
-- are explicitly opted into this flow in ensure_user_with_status().
ALTER TABLE users ADD COLUMN IF NOT EXISTS task_reminder_eligible BOOLEAN DEFAULT FALSE;
-- Tracks which expiry warnings have already gone out, so the reminder job is
-- idempotent and no longer depends on catching a narrow time window.
ALTER TABLE users ADD COLUMN IF NOT EXISTS expiry_reminder_stage INTEGER DEFAULT 0;

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

-- Older deployments created this table before the `id` column existed on it.
-- Adding it here (idempotently) fixes "column id does not exist" on /referralpayout.
ALTER TABLE referrals ADD COLUMN IF NOT EXISTS id SERIAL;

-- ===== MANUAL (ADMIN-VERIFIED) PAYMENTS =====
-- One table serves both USDT and Telegram Stars. `method` distinguishes them,
-- `amount` is stored as text so "12.50" (USDT) and "860" (Stars) both fit
-- without a lossy numeric cast, and `reference` holds the TXID / proof.
CREATE TABLE IF NOT EXISTS manual_payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id),
    method VARCHAR(20) NOT NULL,
    plan VARCHAR(50) NOT NULL,
    cycle VARCHAR(50) NOT NULL,
    amount TEXT NOT NULL,
    reference TEXT,
    proof_file_id TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    reviewed_by BIGINT,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_manual_payments_pending
    ON manual_payments (status, created_at DESC);

-- Legacy USDT table kept for history. New USDT requests go to manual_payments.
CREATE TABLE IF NOT EXISTS usdt_payments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id),
    plan VARCHAR(50) NOT NULL,
    cycle VARCHAR(50) NOT NULL,
    amount_usd NUMERIC(12,2) NOT NULL,
    txid TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    reviewed_by BIGINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stored_files (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    file_name TEXT,
    extension VARCHAR(32),
    file_size BIGINT DEFAULT 0,
    local_path TEXT,
    channel_message_id BIGINT,
    telegram_file_id TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ===== POST EDIT SYNC =====
-- Maps one source message to every copy we sent, so an edit in the source
-- can be mirrored to all destinations. Rows are pruned by age, not kept
-- forever — see prune_sent_map().
CREATE TABLE IF NOT EXISTS sent_messages (
    id SERIAL PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    user_id BIGINT,
    source_chat_id BIGINT NOT NULL,
    source_message_id BIGINT NOT NULL,
    dest_chat_id BIGINT NOT NULL,
    dest_message_id BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sent_messages_lookup
    ON sent_messages (source_chat_id, source_message_id);

CREATE INDEX IF NOT EXISTS idx_sent_messages_age
    ON sent_messages (created_at);

-- ===== STATS =====
-- Per-task counters so /stats can show which task is actually working and
-- which one has silently stopped. Updated on every successful send.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS forward_count BIGINT DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS last_forward_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_forward_at TIMESTAMP WITH TIME ZONE;

-- Warned-today marker so the daily-limit notice is sent once, not per message.
ALTER TABLE users ADD COLUMN IF NOT EXISTS limit_notice_date DATE;

-- ===== PAYOUTS =====
ALTER TABLE users ADD COLUMN IF NOT EXISTS payout_method VARCHAR(32);
ALTER TABLE users ADD COLUMN IF NOT EXISTS payout_address TEXT;

CREATE TABLE IF NOT EXISTS withdrawals (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    amount_paise INTEGER NOT NULL,
    method VARCHAR(32),
    address TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    reviewed_by BIGINT,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_withdrawals_pending
    ON withdrawals (status, created_at DESC);

-- ===== REFERRALS =====
-- Short public referral code, so users share a code instead of their
-- Telegram user id.
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR(16);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code
    ON users (referral_code) WHERE referral_code IS NOT NULL;
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

    # ==========================================
    # USERS
    # ==========================================

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
            await conn.execute(
                """INSERT INTO users
                   (telegram_user_id, username, first_name, task_reminder_eligible)
                   VALUES ($1, $2, $3, TRUE)""",
                user_id, username, first_name,
            )
            return await conn.fetchrow("SELECT * FROM users WHERE telegram_user_id = $1", user_id), True

    async def mark_new_user_notified(self, user_id: int) -> bool:
        if self.pool is None: raise RuntimeError("DB Error")
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT is_new_notified FROM users WHERE telegram_user_id = $1", user_id)
            if val: return False
            await conn.execute("UPDATE users SET is_new_notified = TRUE WHERE telegram_user_id = $1", user_id)
            return True

    async def list_users_due_task_reminder(self, hours: int = 12) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT u.*
                   FROM users u
                   WHERE u.is_blocked = FALSE
                     AND u.task_reminder_eligible = TRUE
                     AND u.task_reminder_sent = FALSE
                     AND u.created_at <= CURRENT_TIMESTAMP - ($1 * INTERVAL '1 hour')
                     AND NOT EXISTS (
                         SELECT 1 FROM tasks t WHERE t.user_id = u.telegram_user_id
                     )
                   ORDER BY u.created_at ASC""",
                hours,
            )

    async def mark_task_reminder_sent(self, user_id: int) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE users
                   SET task_reminder_sent = TRUE
                   WHERE telegram_user_id = $1 AND task_reminder_sent = FALSE
                   RETURNING telegram_user_id""",
                user_id,
            )
            return row is not None

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

    # ==========================================
    # TASKS
    # ==========================================

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
            return await conn.fetchval(
                "INSERT INTO tasks (user_id, task_name, sources, destinations) VALUES ($1, $2, $3, $4) RETURNING id",
                user_id, name, json.dumps(sources), json.dumps(dests)
            )

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
        """Merges keys into a task's settings JSONB.

        Reads and writes inside one transaction with a row lock so two rapid
        button taps can't clobber each other's changes.
        """
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT settings FROM tasks WHERE id = $1 AND user_id = $2 FOR UPDATE",
                    task_id, user_id,
                )
                if not row: return False
                current = row["settings"]
                if isinstance(current, str):
                    current = json.loads(current or "{}")
                elif current is None:
                    current = {}
                current.update(settings_update)
                await conn.execute("UPDATE tasks SET settings = $1 WHERE id = $2", json.dumps(current), task_id)
                return True

    async def clear_task_setting(self, user_id: int, task_id: int, key: str) -> bool:
        """Removes a single key from a task's settings JSONB entirely."""
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT settings FROM tasks WHERE id = $1 AND user_id = $2 FOR UPDATE",
                    task_id, user_id,
                )
                if not row: return False
                current = row["settings"]
                if isinstance(current, str):
                    current = json.loads(current or "{}")
                elif current is None:
                    current = {}
                current.pop(key, None)
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

    # ==========================================
    # USAGE COUNTERS
    # ==========================================

    async def daily_usage(self, user_id: int) -> int:
        if self.pool is None: return 0
        today = datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT message_count FROM usage_daily WHERE user_id = $1 AND usage_date = $2", user_id, today)
            return val or 0

    async def increment_usage(self, user_id: int, task_id: int | None = None) -> None:
        """Counts one successful forward.

        Also stamps last_forward_at on the user and the task. That timestamp is
        what lets /stats answer "is this thing still working?" — the single most
        useful line on the screen when a task has silently stopped.
        """
        if self.pool is None: return
        now = datetime.now(timezone.utc)
        today = now.date()
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO usage_daily (user_id, usage_date, message_count)
                VALUES ($1, $2, 1)
                ON CONFLICT (user_id, usage_date) DO UPDATE SET message_count = usage_daily.message_count + 1
            """, user_id, today)
            await conn.execute(
                "UPDATE users SET last_forward_at = $1 WHERE telegram_user_id = $2", now, user_id,
            )
            if task_id is not None:
                await conn.execute(
                    """UPDATE tasks SET forward_count = COALESCE(forward_count, 0) + 1,
                              last_forward_at = $1
                       WHERE id = $2""",
                    now, task_id,
                )

    async def increment_usage_bulk(self, user_id: int, task_id: int | None, count: int) -> None:
        """Records `count` successful forwards in ONE round-trip.

        The per-message version issued three statements for every destination —
        with 50 targets that was 150 database round-trips for a single incoming
        post, and it dominated the forwarding time.
        """
        if self.pool is None or count <= 0: return
        now = datetime.now(timezone.utc)
        today = now.date()
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO usage_daily (user_id, usage_date, message_count)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, usage_date)
                DO UPDATE SET message_count = usage_daily.message_count + $3
            """, user_id, today, count)
            await conn.execute(
                "UPDATE users SET last_forward_at = $1 WHERE telegram_user_id = $2", now, user_id,
            )
            if task_id is not None:
                await conn.execute(
                    """UPDATE tasks SET forward_count = COALESCE(forward_count, 0) + $1,
                              last_forward_at = $2
                       WHERE id = $3""",
                    count, now, task_id,
                )

    async def record_sent_messages(self, rows: list[tuple]) -> None:
        """Batch insert for the edit-sync map — one round-trip for all copies
        instead of one per destination."""
        if self.pool is None or not rows: return
        try:
            async with self.pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO sent_messages
                       (task_id, user_id, source_chat_id, source_message_id, dest_chat_id, dest_message_id)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    rows,
                )
        except Exception as exc:
            logger.warning("Could not batch-record sent messages: %s", exc)

    async def usage_stats(self, user_id: int) -> dict:
        """Everything the /stats screen needs, in one round-trip."""
        if self.pool is None:
            return {"today": 0, "month": 0, "total": 0, "last_forward_at": None}
        now = datetime.now(timezone.utc)
        month_start = now.date().replace(day=1)
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                     COALESCE(SUM(CASE WHEN usage_date = $2 THEN message_count END), 0) AS today,
                     COALESCE(SUM(CASE WHEN usage_date >= $3 THEN message_count END), 0) AS month,
                     COALESCE(SUM(message_count), 0) AS total
                   FROM usage_daily WHERE user_id = $1""",
                user_id, now.date(), month_start,
            )
            last = await conn.fetchval(
                "SELECT last_forward_at FROM users WHERE telegram_user_id = $1", user_id,
            )
        return {
            "today": int(row["today"] or 0),
            "month": int(row["month"] or 0),
            "total": int(row["total"] or 0),
            "last_forward_at": last,
        }

    async def should_send_limit_notice(self, user_id: int) -> bool:
        """True at most once per day, so hitting the cap on message 501 through
        5000 does not produce 4500 notifications."""
        if self.pool is None: return False
        today = datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE users SET limit_notice_date = $1
                   WHERE telegram_user_id = $2
                     AND (limit_notice_date IS NULL OR limit_notice_date <> $1)
                   RETURNING telegram_user_id""",
                today, user_id,
            )
            return row is not None

    # ==========================================
    # POST EDIT SYNC — SENT MESSAGE MAP
    # ==========================================

    async def record_sent_message(
        self, task_id: int, user_id: int,
        source_chat_id: int, source_message_id: int,
        dest_chat_id: int, dest_message_id: int,
    ) -> None:
        """Remembers which copy belongs to which source message.

        Best-effort: a failure here must never break a forward that already
        succeeded, so errors are swallowed and logged.
        """
        if self.pool is None: return
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO sent_messages
                       (task_id, user_id, source_chat_id, source_message_id, dest_chat_id, dest_message_id)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    task_id, user_id, source_chat_id, source_message_id, dest_chat_id, dest_message_id,
                )
        except Exception as exc:
            logger.warning("Could not record sent message map: %s", exc)

    async def get_sent_copies(self, source_chat_id: int, source_message_id: int) -> list[asyncpg.Record]:
        """All destination copies of one source message — used by edit sync."""
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT task_id, user_id, dest_chat_id, dest_message_id
                   FROM sent_messages
                   WHERE source_chat_id = $1 AND source_message_id = $2""",
                source_chat_id, source_message_id,
            )

    async def prune_sent_map(self, older_than_days: int = 3) -> int:
        """Deletes old rows from the edit-sync map.

        Telegram only allows editing messages for 48 hours, so anything older
        is dead weight. Without this the table grows without bound.
        """
        if self.pool is None: return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM sent_messages WHERE created_at < $1", cutoff)
        try:
            return int(str(res).split()[-1])
        except (ValueError, IndexError):
            return 0

    # ==========================================
    # REFERRALS
    # ==========================================

    async def ensure_referral_code(self, user_id: int) -> str:
        """Returns the user's short public referral code, creating it on first
        use. Sharing a code instead of a Telegram user id keeps the id private."""
        if self.pool is None:
            return ""
        async with self.pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT referral_code FROM users WHERE telegram_user_id = $1", user_id,
            )
            if existing:
                return str(existing)
            alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alike chars
            for _ in range(8):
                code = "".join(secrets.choice(alphabet) for _ in range(8))
                try:
                    await conn.execute(
                        "UPDATE users SET referral_code = $1 WHERE telegram_user_id = $2",
                        code, user_id,
                    )
                    return code
                except asyncpg.UniqueViolationError:
                    continue  # astronomically unlikely; just try another
        return ""

    async def user_by_referral_code(self, code: str) -> int | None:
        if self.pool is None or not code:
            return None
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT telegram_user_id FROM users WHERE referral_code = $1", code.upper(),
            )

    async def create_referral(self, referrer_id: int, referred_id: int) -> bool:
        if self.pool is None: return False
        if referrer_id == referred_id: return False
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1 FROM referrals WHERE referred_id = $1 LIMIT 1", referred_id)
            if val: return False
            await conn.execute(
                "INSERT INTO referrals (referrer_id, referred_id, commission_amount_paise) VALUES ($1, $2, 0)",
                referrer_id, referred_id,
            )
            return True

    async def count_referrals(self, user_id: int) -> int:
        if self.pool is None: return 0
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM referrals WHERE referrer_id = $1", user_id) or 0

    async def credit_referral_commission(self, referred_id: int, amount_paise: int) -> asyncpg.Record | None:
        """Adds the referrer's cut of a payment made by `referred_id`.

        Called on EVERY successful payment (card, USDT or Stars), so a referrer
        keeps earning as long as their referral keeps paying — not just on the
        first purchase. Returns the updated row (with referrer_id) so the caller
        can notify them, or None if this user was never referred.
        """
        if self.pool is None or amount_paise <= 0:
            return None
        commission = referral_commission_paise(amount_paise)
        if commission <= 0:
            return None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """SELECT id, referrer_id FROM referrals
                       WHERE referred_id = $1 ORDER BY id ASC LIMIT 1 FOR UPDATE""",
                    referred_id,
                )
                if not row:
                    return None
                return await conn.fetchrow(
                    """UPDATE referrals
                       SET commission_amount_paise = commission_amount_paise + $1,
                           is_paid = FALSE
                       WHERE id = $2
                       RETURNING id, referrer_id, referred_id, commission_amount_paise""",
                    commission, int(row["id"]),
                )

    async def referral_summary(self, referrer_id: int) -> dict:
        """Totals for the /refer screen: how many joined and what is owed."""
        if self.pool is None:
            return {"joined": 0, "unpaid_paise": 0, "paid_paise": 0}
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT COUNT(*) AS joined,
                          COALESCE(SUM(CASE WHEN is_paid = FALSE THEN commission_amount_paise ELSE 0 END), 0) AS unpaid,
                          COALESCE(SUM(CASE WHEN is_paid = TRUE  THEN commission_amount_paise ELSE 0 END), 0) AS paid
                   FROM referrals WHERE referrer_id = $1""",
                referrer_id,
            )
        return {
            "joined": int(row["joined"] or 0),
            "unpaid_paise": int(row["unpaid"] or 0),
            "paid_paise": int(row["paid"] or 0),
        }

    async def payout_referrals(self, referrer_id: int) -> int:
        """Marks every unpaid commission for a referrer as paid.

        Returns the total paid out in paise, or 0 if there was nothing owed —
        which lets the admin command tell the difference instead of silently
        reporting success.
        """
        if self.pool is None:
            return 0
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                total = await conn.fetchval(
                    """SELECT COALESCE(SUM(commission_amount_paise), 0) FROM referrals
                       WHERE referrer_id = $1 AND is_paid = FALSE AND commission_amount_paise > 0
                       FOR UPDATE""",
                    referrer_id,
                )
                total = int(total or 0)
                if total <= 0:
                    return 0
                await conn.execute(
                    """UPDATE referrals SET is_paid = TRUE
                       WHERE referrer_id = $1 AND is_paid = FALSE""",
                    referrer_id,
                )
                return total

    async def set_payout_method(self, user_id: int, method: str, address: str) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET payout_method = $1, payout_address = $2 WHERE telegram_user_id = $3",
                method, address, user_id,
            )

    async def get_payout_method(self, user_id: int) -> tuple[str | None, str | None]:
        if self.pool is None: return None, None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT payout_method, payout_address FROM users WHERE telegram_user_id = $1", user_id,
            )
        if not row:
            return None, None
        return row["payout_method"], row["payout_address"]

    async def pending_withdrawal(self, user_id: int) -> asyncpg.Record | None:
        """A user may only have one open request — otherwise they could queue
        several requests for the same balance and be paid twice."""
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT * FROM withdrawals
                   WHERE user_id = $1 AND status = 'pending'
                   ORDER BY id DESC LIMIT 1""",
                user_id,
            )

    async def create_withdrawal(self, user_id: int, amount_paise: int, method: str, address: str) -> int | None:
        if self.pool is None or amount_paise <= 0: return None
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchval(
                    "SELECT 1 FROM withdrawals WHERE user_id = $1 AND status = 'pending' FOR UPDATE",
                    user_id,
                )
                if existing:
                    return None
                return await conn.fetchval(
                    """INSERT INTO withdrawals (user_id, amount_paise, method, address)
                       VALUES ($1, $2, $3, $4) RETURNING id""",
                    user_id, amount_paise, method, address,
                )

    async def get_withdrawal(self, request_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM withdrawals WHERE id = $1", request_id)

    async def list_pending_withdrawals(self, limit: int = 20) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT w.*, u.username, u.first_name
                   FROM withdrawals w
                   LEFT JOIN users u ON u.telegram_user_id = w.user_id
                   WHERE w.status = 'pending'
                   ORDER BY w.created_at ASC LIMIT $1""",
                limit,
            )

    async def set_withdrawal_status(self, request_id: int, status: str, reviewed_by: int) -> bool:
        """Guarded on 'pending' so two admins tapping Approve cannot pay twice."""
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """UPDATE withdrawals SET status = $1, reviewed_by = $2, reviewed_at = CURRENT_TIMESTAMP
                   WHERE id = $3 AND status = 'pending'""",
                status, reviewed_by, request_id,
            )
            return res == "UPDATE 1"

    async def list_pending_payouts(self, limit: int = 20) -> list[asyncpg.Record]:
        """Referrers who are owed money — the admin payout queue."""
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT r.referrer_id,
                          SUM(r.commission_amount_paise) AS owed,
                          COUNT(*) AS refs,
                          u.username, u.first_name
                   FROM referrals r
                   LEFT JOIN users u ON u.telegram_user_id = r.referrer_id
                   WHERE r.is_paid = FALSE AND r.commission_amount_paise > 0
                   GROUP BY r.referrer_id, u.username, u.first_name
                   ORDER BY owed DESC
                   LIMIT $1""",
                limit,
            )

    async def mark_referral_paid(self, user_id: int) -> asyncpg.Record | None:
        """Legacy single-row payout, kept so older call sites keep working."""
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT id, referrer_id, commission_amount_paise FROM referrals WHERE referred_id = $1 AND is_paid = FALSE LIMIT 1 FOR UPDATE", user_id)
            if row:
                await conn.execute("UPDATE referrals SET is_paid = TRUE WHERE id = $1", row["id"])
                return row
            return None

    async def list_recent_active_users(self, limit: int = 6) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT * FROM users WHERE is_blocked = FALSE ORDER BY last_seen_at DESC LIMIT $1",
                limit,
            )

    async def list_users_page(self, offset: int = 0, limit: int = 10) -> list[asyncpg.Record]:
        """One page of users, most recently active first — what the admin
        pickers page through."""
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT * FROM users
                   ORDER BY last_seen_at DESC NULLS LAST, created_at DESC
                   OFFSET $1 LIMIT $2""",
                max(0, offset), max(1, limit),
            )

    async def list_all_tasks_page(self, offset: int = 0, limit: int = 10) -> list[asyncpg.Record]:
        """One page of EVERY user's tasks, newest first — the /usertasks view."""
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT t.*, u.username, u.first_name, u.plan
                   FROM tasks t
                   LEFT JOIN users u ON u.telegram_user_id = t.user_id
                   ORDER BY t.id DESC
                   OFFSET $1 LIMIT $2""",
                max(0, offset), max(1, limit),
            )

    async def count_all_tasks(self) -> int:
        if self.pool is None: return 0
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM tasks") or 0)

    async def reduce_plan_days(self, user_id: int, days: int) -> tuple[bool, bool]:
        """Takes `days` off a user's plan.

        Returns (changed, expired_now). Expiry never goes negative: once the
        remaining time runs out the user drops straight to Free, because a
        plan with a date in the past is meaningless.
        """
        if self.pool is None or days <= 0:
            return False, False
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT plan, plan_expiry FROM users WHERE telegram_user_id = $1 FOR UPDATE",
                    user_id,
                )
                if not row or row["plan"] == "free":
                    return False, False
                expiry = row["plan_expiry"]
                if expiry is None:
                    return False, False
                new_expiry = expiry - timedelta(days=days)
                if new_expiry <= now:
                    await conn.execute(
                        """UPDATE users SET plan = 'free', plan_expiry = NULL,
                                  scheduled_plan = NULL, scheduled_days = NULL,
                                  expiry_reminder_stage = 0
                           WHERE telegram_user_id = $1""",
                        user_id,
                    )
                    return True, True
                await conn.execute(
                    """UPDATE users SET plan_expiry = $1, expiry_reminder_stage = 0
                       WHERE telegram_user_id = $2""",
                    new_expiry, user_id,
                )
                return True, False

    async def count_all_users(self) -> int:
        if self.pool is None: return 0
        async with self.pool.acquire() as conn:
            return int(await conn.fetchval("SELECT COUNT(*) FROM users") or 0)

    async def list_users_by_ids(self, user_ids: list[int]) -> list[asyncpg.Record]:
        """Used by the admin 'Select Users' broadcast flow to resolve the final
        picked telegram_user_ids into broadcastable rows."""
        if self.pool is None or not user_ids: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                "SELECT telegram_user_id FROM users WHERE telegram_user_id = ANY($1::bigint[]) AND is_blocked = FALSE",
                list(user_ids),
            )

    # ==========================================
    # MANUAL PAYMENTS (USDT + TELEGRAM STARS)
    # ==========================================

    async def create_manual_payment(
        self, user_id: int, method: str, plan: str, cycle: str,
        amount: str, reference: str | None = None, proof_file_id: str | None = None,
    ) -> int:
        """Creates a pending admin-review payment. `method` is 'usdt' or 'stars'."""
        if self.pool is None: raise RuntimeError("DB Error")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO manual_payments
                   (user_id, method, plan, cycle, amount, reference, proof_file_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                user_id, method.lower(), plan, cycle, str(amount), reference, proof_file_id,
            )

    async def get_manual_payment(self, request_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM manual_payments WHERE id = $1", request_id)

    async def list_pending_manual_payments(self, limit: int = 20) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT m.*, u.username, u.first_name
                   FROM manual_payments m
                   LEFT JOIN users u ON u.telegram_user_id = m.user_id
                   WHERE m.status = 'pending'
                   ORDER BY m.created_at ASC
                   LIMIT $1""",
                limit,
            )

    async def count_pending_manual_payments(self) -> int:
        if self.pool is None: return 0
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM manual_payments WHERE status = 'pending'") or 0

    async def set_manual_payment_status(self, request_id: int, status: str, reviewed_by: int) -> bool:
        """Marks a manual payment approved/rejected.

        The `status = 'pending'` guard makes this idempotent: if two admins tap
        Approve at the same moment, only the first one wins and the second sees
        'already handled' instead of granting the plan twice.
        """
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """UPDATE manual_payments
                   SET status = $1, reviewed_by = $2, reviewed_at = CURRENT_TIMESTAMP
                   WHERE id = $3 AND status = 'pending'""",
                status, reviewed_by, request_id,
            )
            return res == "UPDATE 1"

    # --- legacy USDT helpers (kept so old rows stay readable) ---

    async def create_usdt_request(self, user_id: int, plan: str, cycle: str, amount_usd: float, txid: str) -> int:
        """Deprecated — new requests should use create_manual_payment('usdt')."""
        return await self.create_manual_payment(user_id, "usdt", plan, cycle, f"{amount_usd:.2f}", txid)

    async def get_usdt_request(self, request_id: int) -> asyncpg.Record | None:
        return await self.get_manual_payment(request_id)

    async def set_usdt_status(self, request_id: int, status: str, reviewed_by: int) -> bool:
        return await self.set_manual_payment_status(request_id, status, reviewed_by)

    # ==========================================
    # STORED FILES (ATTACH CUSTOM FILE)
    # ==========================================

    async def save_stored_file(self, user_id: int, file_name: str, extension: str, file_size: int, local_path: str | None, channel_message_id: int | None, telegram_file_id: str | None) -> int:
        if self.pool is None: raise RuntimeError("DB Error")
        async with self.pool.acquire() as conn:
            # A user can only have ONE stored file. Before replacing the DB row,
            # remove the old file from disk too, so replaced uploads don't pile up.
            old = await conn.fetchrow("SELECT local_path FROM stored_files WHERE user_id = $1", user_id)
            if old and old["local_path"]:
                with suppress(Exception):
                    if os.path.exists(old["local_path"]):
                        os.remove(old["local_path"])
            await conn.execute("DELETE FROM stored_files WHERE user_id = $1", user_id)
            return await conn.fetchval(
                """INSERT INTO stored_files (user_id, file_name, extension, file_size, local_path, channel_message_id, telegram_file_id)
                   VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                user_id, file_name, extension, file_size, local_path, channel_message_id, telegram_file_id,
            )

    async def get_stored_file(self, user_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM stored_files WHERE user_id = $1 ORDER BY id DESC LIMIT 1", user_id)

    async def update_stored_file_path(self, user_id: int, local_path: str | None) -> None:
        if self.pool is None: return
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE stored_files SET local_path = $1 WHERE user_id = $2",
                local_path, user_id,
            )

    async def delete_stored_file(self, user_id: int) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute("DELETE FROM stored_files WHERE user_id = $1", user_id)
            return res == "DELETE 1"

    # ==========================================
    # RAZORPAY PAYMENTS
    # ==========================================

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

    async def find_pending_payment(self, user_id: int, plan: str, cycle: str) -> asyncpg.Record | None:
        """Fallback lookup for webhooks that cannot give us the payment-link id
        (e.g. `payment.captured`). Matches the newest un-captured order for the
        same user/plan/cycle, which is what the notes on the payment carry."""
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                """SELECT * FROM payments
                   WHERE user_id = $1 AND plan = $2 AND cycle = $3 AND status != 'captured'
                   ORDER BY id DESC LIMIT 1""",
                user_id, plan, cycle,
            )

    async def has_paid_order(self, user_id: int) -> bool:
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1 FROM payments WHERE user_id = $1 AND status = 'captured' LIMIT 1", user_id)
            return bool(val)

    async def last_purchase_info(self, user_id: int) -> dict | None:
        """The most recent successful purchase across ALL payment methods.

        Used on the reduce-days confirmation so an admin can see what the user
        actually paid before shortening their plan.
        """
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            card = await conn.fetchrow(
                """SELECT plan, cycle, payable_amount_paise, amount_paise, created_at
                   FROM payments WHERE user_id = $1 AND status = 'captured'
                   ORDER BY id DESC LIMIT 1""",
                user_id,
            )
            manual = await conn.fetchrow(
                """SELECT plan, cycle, method, amount, created_at
                   FROM manual_payments WHERE user_id = $1 AND status = 'approved'
                   ORDER BY id DESC LIMIT 1""",
                user_id,
            )
        best, source = None, None
        if card and (not manual or card["created_at"] >= manual["created_at"]):
            best, source = card, "card"
        elif manual:
            best, source = manual, "manual"
        if best is None:
            return None
        if source == "card":
            paise = int(best["payable_amount_paise"] or best["amount_paise"] or 0)
            return {
                "plan": str(best["plan"]), "cycle": str(best["cycle"]),
                "method": "UPI / Card", "amount": f"₹{paise / 100:.2f}",
                "when": best["created_at"],
            }
        return {
            "plan": str(best["plan"]), "cycle": str(best["cycle"]),
            "method": str(best["method"]).upper(), "amount": str(best["amount"]),
            "when": best["created_at"],
        }

    async def get_last_captured_payment(self, user_id: int) -> asyncpg.Record | None:
        if self.pool is None: return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(
                "SELECT payment_id, plan, cycle, created_at FROM payments WHERE user_id = $1 AND status = 'captured' ORDER BY id DESC LIMIT 1",
                user_id,
            )

    async def activate_payment(self, order_id: str, payment_id: str, amount_paise: int, purchased_days: int, purchased_plan: str, cycle: str) -> int | None:
        """Captures a Razorpay payment and applies the plan. Idempotent."""
        if self.pool is None: return None
        now = datetime.now(timezone.utc)

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                payment = await conn.fetchrow(
                    """SELECT user_id, status, payable_amount_paise, amount_paise, plan, cycle
                       FROM payments WHERE order_id = $1 FOR UPDATE""",
                    order_id,
                )
                if not payment or payment["status"] == "captured":
                    return None  # idempotent: already activated
                expected_amount = int(payment["payable_amount_paise"] or payment["amount_paise"])
                if int(amount_paise) != expected_amount:
                    logger.warning(
                        "Payment amount mismatch for order %s: expected %s, received %s",
                        order_id, expected_amount, amount_paise,
                    )
                    raise ValueError("Payment amount does not match the order")

                user_id = payment["user_id"]
                await conn.execute("UPDATE payments SET payment_id = $1, status = 'captured' WHERE order_id = $2", payment_id, order_id)
                return await self._apply_plan_locked(conn, user_id, purchased_plan, purchased_days, now)

    async def apply_manual_plan(self, request_id: int, purchased_plan: str, purchased_days: int) -> int | None:
        """Applies a plan for an approved USDT / Stars payment.

        Uses the exact same upgrade/downgrade/renewal maths as a Razorpay
        capture, so a manually-approved payment behaves identically to a card
        payment — no separate code path to drift out of sync.
        """
        if self.pool is None: return None
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                req = await conn.fetchrow(
                    "SELECT user_id, status FROM manual_payments WHERE id = $1 FOR UPDATE",
                    request_id,
                )
                if not req:
                    return None
                return await self._apply_plan_locked(conn, req["user_id"], purchased_plan, purchased_days, now)

    async def _apply_plan_locked(self, conn, user_id: int, purchased_plan: str, purchased_days: int, now: datetime) -> int | None:
        """Shared plan-application maths. MUST be called inside a transaction.

        Three cases:
          * same rank   -> renewal, extend from current expiry (no time lost)
          * lower rank  -> downgrade, scheduled for when the current plan ends
          * higher rank -> upgrade, unused value converted into new-plan days
        """
        user = await conn.fetchrow("SELECT plan, plan_expiry FROM users WHERE telegram_user_id = $1 FOR UPDATE", user_id)
        if not user:
            return None

        current_plan = user["plan"] or "free"
        current_expiry = user["plan_expiry"] or now
        if current_expiry < now:
            current_expiry = now

        current_rank = PLAN_RANKS.get(current_plan, 0)
        purchased_rank = PLAN_RANKS.get(purchased_plan, 0)

        if current_rank == purchased_rank:
            # SAME-PLAN RENEWAL: extend from current expiry (do not lose remaining time)
            new_expiry = current_expiry + timedelta(days=purchased_days)
            await conn.execute(
                """UPDATE users SET plan_expiry = $1, scheduled_plan = NULL, scheduled_days = NULL,
                          expiry_reminder_stage = 0
                   WHERE telegram_user_id = $2""",
                new_expiry, user_id,
            )
        elif purchased_rank < current_rank:
            # DOWNGRADE: keep the higher plan until it expires, then switch
            if current_expiry <= now:
                new_expiry = now + timedelta(days=purchased_days)
                await conn.execute(
                    """UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL,
                              scheduled_days = NULL, expiry_reminder_stage = 0
                       WHERE telegram_user_id = $3""",
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
            new_expiry = now + timedelta(days=purchased_days + converted_days)
            await conn.execute(
                """UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL,
                          scheduled_days = NULL, expiry_reminder_stage = 0
                   WHERE telegram_user_id = $3""",
                purchased_plan, new_expiry, user_id,
            )
        return user_id

    # ==========================================
    # PLAN EXPIRY / DOWNGRADE
    # ==========================================

    async def get_expiring_users(self, days: int) -> list[asyncpg.Record]:
        """Paid users whose plan expires within `days`, who have not already
        been warned at this stage.

        FIXED: the old version only matched a ±1 hour window around an exact
        target time. Because the job runs once a day, anyone whose expiry fell
        outside that hour NEVER got a reminder. Now it matches everything up to
        the cutoff and uses expiry_reminder_stage to avoid repeat sends.

        `days` doubles as the stage number: stage 3 = the 3-day warning,
        stage 1 = the 1-day warning. Send the larger number first.
        """
        if self.pool is None: return []
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)
        async with self.pool.acquire() as conn:
            return await conn.fetch(
                """SELECT telegram_user_id, plan, preferred_language, plan_expiry
                   FROM users
                   WHERE plan != 'free'
                     AND plan_expiry IS NOT NULL
                     AND plan_expiry > $1
                     AND plan_expiry <= $2
                     -- 0 means "never warned". A stored stage is only skipped
                     -- when we have already sent that stage or a more urgent
                     -- one; -1 marks the final same-day warning.
                     AND (expiry_reminder_stage = 0
                          OR (expiry_reminder_stage > $3 AND expiry_reminder_stage <> -1))
                   ORDER BY plan_expiry ASC""",
                now, cutoff, days,
            )

    async def mark_expiry_reminder_sent(self, user_id: int, stage: int) -> bool:
        """Records that the `stage`-day warning has gone out.

        The guard makes repeat sends impossible even if the job runs twice.
        """
        if self.pool is None: return False
        async with self.pool.acquire() as conn:
            res = await conn.execute(
                """UPDATE users SET expiry_reminder_stage = $1
                   WHERE telegram_user_id = $2
                     AND (expiry_reminder_stage = 0 OR expiry_reminder_stage > $1)""",
                stage, user_id,
            )
            return res == "UPDATE 1"

    async def downgrade_expired_users(self) -> list[asyncpg.Record]:
        if self.pool is None: return []
        now = datetime.now(timezone.utc)
        downgraded = []
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                expired = await conn.fetch(
                    """SELECT telegram_user_id, scheduled_plan, scheduled_days, preferred_language
                       FROM users
                       WHERE plan != 'free' AND plan_expiry < $1 FOR UPDATE""",
                    now,
                )
                for row in expired:
                    uid = row["telegram_user_id"]
                    if row["scheduled_plan"] and row["scheduled_days"]:
                        # Activate scheduled plan (downgrade takes effect at expiry)
                        new_expiry = now + timedelta(days=row["scheduled_days"])
                        await conn.execute(
                            """UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL,
                                      scheduled_days = NULL, expiry_reminder_stage = 0
                               WHERE telegram_user_id = $3""",
                            row["scheduled_plan"], new_expiry, uid,
                        )
                    else:
                        # No scheduled plan -> downgrade to free
                        await conn.execute(
                            """UPDATE users SET plan = 'free', plan_expiry = NULL, scheduled_plan = NULL,
                                      scheduled_days = NULL, expiry_reminder_stage = 0
                               WHERE telegram_user_id = $1""",
                            uid,
                        )
                    downgraded.append(row)
        return downgraded

    async def set_plan(self, user_id: int, plan: str, days: int) -> bool:
        """Admin override (/grantdays). Wins outright over any schedule."""
        if self.pool is None: return False
        if days <= 0:
            return False  # SAFETY: refuse zero/negative days to prevent a reset
        now = datetime.now(timezone.utc)
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT plan_expiry FROM users WHERE telegram_user_id = $1", user_id)
            if not user: return False
            base_time = user["plan_expiry"] if user["plan_expiry"] and user["plan_expiry"] > now else now
            new_expiry = base_time + timedelta(days=days)
            res = await conn.execute(
                """UPDATE users SET plan = $1, plan_expiry = $2, scheduled_plan = NULL,
                          scheduled_days = NULL, expiry_reminder_stage = 0
                   WHERE telegram_user_id = $3""",
                plan, new_expiry, user_id,
            )
            return res == "UPDATE 1"

    # ==========================================
    # ADMIN STATS & BROADCASTS
    # ==========================================

    # ==========================================
    # BACKUP / RESTORE
    # ==========================================
    # Every table that holds real state. sent_messages is deliberately left
    # out: it rebuilds itself and is pruned after 3 days anyway.
    BACKUP_TABLES = [
        "users", "sessions", "tasks", "payments", "manual_payments",
        "usdt_payments", "withdrawals", "referrals", "usage_daily",
        "stored_files", "broadcasts",
    ]

    async def export_backup(self) -> dict:
        """Reads every table into a plain dict, ready to be written as JSON.

        Deliberately not pg_dump: this runs inside the bot with no shell, which
        is what makes a one-tap restore from Telegram possible.
        """
        if self.pool is None:
            return {}
        data: dict[str, list] = {}
        async with self.pool.acquire() as conn:
            for table in self.BACKUP_TABLES:
                try:
                    rows = await conn.fetch(f"SELECT * FROM {table}")  # noqa: S608 - fixed list
                except Exception as exc:
                    logger.warning("Backup: could not read %s: %s", table, exc)
                    continue
                data[table] = [dict(r) for r in rows]
        return data

    async def import_backup(self, data: dict) -> dict:
        """Replaces the current contents with a backup.

        Runs in ONE transaction: if any table fails, nothing is committed and
        the live database is left exactly as it was. A half-restored database
        would be worse than no restore at all.
        """
        if self.pool is None:
            return {}
        restored: dict[str, int] = {}
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                # Reverse order so child rows go before their parents.
                for table in reversed(self.BACKUP_TABLES):
                    if table in data:
                        await conn.execute(f"DELETE FROM {table}")  # noqa: S608

                for table in self.BACKUP_TABLES:
                    rows = data.get(table) or []
                    if not rows:
                        restored[table] = 0
                        continue
                    columns = list(rows[0].keys())
                    col_sql = ", ".join(f'"{c}"' for c in columns)
                    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
                    stmt = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"  # noqa: S608
                    await conn.executemany(
                        stmt, [tuple(r.get(c) for c in columns) for r in rows],
                    )
                    restored[table] = len(rows)

        # Sequences must be moved past the restored ids, or the next insert
        # collides with a row that already exists.
        #
        # This runs OUTSIDE the transaction above, deliberately. Tables such as
        # `sessions` have no `id` column, so pg_get_serial_sequence returns NULL
        # and setval errors. A failed statement poisons the WHOLE PostgreSQL
        # transaction — suppressing the Python exception does not un-poison it,
        # and the commit silently rolled everything back. The restore then
        # reported success while having restored nothing at all.
        async with self.pool.acquire() as conn:
            for table in self.BACKUP_TABLES:
                try:
                    async with conn.transaction():
                        await conn.execute(
                            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                            f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"  # noqa: S608
                        )
                except Exception:
                    # No id column on this table — nothing to reset.
                    continue
        return restored

    async def stats(self) -> dict[str, Any]:
        if self.pool is None: return {}
        today = datetime.now(timezone.utc).date()
        async with self.pool.acquire() as conn:
            users = await conn.fetchval("SELECT COUNT(*) FROM users")
            new_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE DATE(created_at) = $1", today)
            paid = await conn.fetchval("SELECT COUNT(*) FROM users WHERE plan != 'free'")
            active_tasks = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE is_paused = FALSE")
            captured = await conn.fetchval("SELECT COUNT(*) FROM payments WHERE status = 'captured'")
            pending_manual = await conn.fetchval("SELECT COUNT(*) FROM manual_payments WHERE status = 'pending'")
            return {
                "users": users or 0,
                "new_users_today": new_users or 0,
                "paid_users": paid or 0,
                "active_tasks": active_tasks or 0,
                "captured_payments": captured or 0,
                "pending_manual_payments": pending_manual or 0,
            }

    async def list_broadcast_users(self, audience: str) -> list[asyncpg.Record]:
        if self.pool is None: return []
        async with self.pool.acquire() as conn:
            if audience == "all":
                return await conn.fetch("SELECT telegram_user_id FROM users WHERE is_blocked = FALSE")
            if audience == "active":
                return await conn.fetch("SELECT telegram_user_id FROM users WHERE is_blocked = FALSE AND last_seen_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'")
            if audience == "paid":
                return await conn.fetch("SELECT telegram_user_id FROM users WHERE plan != 'free' AND is_blocked = FALSE")
            if audience in ("english", "hinglish"):
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
