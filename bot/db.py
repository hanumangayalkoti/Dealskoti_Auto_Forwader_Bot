# ---------------------------------------------------------
# SAFE MIGRATIONS: Adds new columns without dropping old ones
# ---------------------------------------------------------
MIGRATIONS_SQL = """
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

-- Upgrading schema safely for scheduled plans (Downgrade queues)
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

-- FIX: Safely add session_string if the old database had a different column name
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
    status VARCHAR(50) DEFAULT 'created',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- FIX: Safely patch old payments table missing columns
ALTER TABLE payments ADD COLUMN IF NOT EXISTS order_id VARCHAR(255);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_id VARCHAR(255);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS plan VARCHAR(50);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS cycle VARCHAR(50);
ALTER TABLE payments ADD COLUMN IF NOT EXISTS amount_paise INTEGER;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS original_amount_paise INTEGER DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS discount_amount_paise INTEGER DEFAULT 0;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'created';

CREATE TABLE IF NOT EXISTS usage_daily (
    id SERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(telegram_user_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    UNIQUE (user_id, usage_date)
);

-- FIX: Safely adding message_count for old databases
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
