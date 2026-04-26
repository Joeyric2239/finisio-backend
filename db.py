"""
FINISIO CLEANS - Database Layer
SQLite3 schema, migrations, and all query helpers.
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "finisio.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # rows behave like dicts
    conn.execute("PRAGMA journal_mode=DELETE") # compatible with all filesystems
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------
#  SCHEMA  (idempotent - safe to call on every startup)
# ---------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    email               TEXT UNIQUE NOT NULL,
    role                TEXT NOT NULL CHECK(role IN ('customer','cleaner','admin')),
    phone               TEXT,
    address             TEXT,
    password_hash       TEXT,
    verification_status TEXT DEFAULT 'unverified',
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cleaner_profiles (
    user_id             TEXT PRIMARY KEY REFERENCES users(id),
    approved_status     TEXT DEFAULT 'pending' CHECK(approved_status IN ('pending','approved','rejected')),
    service_areas       TEXT,
    skills              TEXT,
    rating              REAL DEFAULT 0.0,
    total_jobs_completed INTEGER DEFAULT 0,
    id_document_url     TEXT,
    experience_years    INTEGER DEFAULT 0,
    approved_at         TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES users(id),
    plan_type       TEXT NOT NULL CHECK(plan_type IN ('basic','standard','premium')),
    price_scr       REAL NOT NULL,
    hours_allocated REAL NOT NULL,
    hours_used      REAL DEFAULT 0.0,
    status          TEXT DEFAULT 'active' CHECK(status IN ('active','paused','cancelled','expired')),
    renewal_date    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bookings (
    id              TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES users(id),
    cleaner_id      TEXT REFERENCES users(id),
    subscription_id TEXT REFERENCES subscriptions(id),
    service_type    TEXT NOT NULL CHECK(service_type IN ('home_deep','post_construction')),
    booking_type    TEXT NOT NULL CHECK(booking_type IN ('subscription','one_time')),
    status          TEXT DEFAULT 'pending' CHECK(status IN (
                        'pending','assigned','accepted','in_progress',
                        'completed','cancelled','disputed')),
    scheduled_date  TEXT NOT NULL,
    scheduled_time  TEXT,
    address         TEXT,
    notes           TEXT,
    media_urls      TEXT,
    hours_booked    REAL,
    amount_scr      REAL,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS booking_status_log (
    id          TEXT PRIMARY KEY,
    booking_id  TEXT NOT NULL REFERENCES bookings(id),
    old_status  TEXT,
    new_status  TEXT NOT NULL,
    changed_by  TEXT REFERENCES users(id),
    note        TEXT,
    changed_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payments (
    id              TEXT PRIMARY KEY,
    booking_id      TEXT NOT NULL REFERENCES bookings(id),
    customer_id     TEXT NOT NULL REFERENCES users(id),
    amount_scr      REAL NOT NULL,
    payment_method  TEXT DEFAULT 'bank_transfer' CHECK(payment_method IN ('bank_transfer','manual','cash')),
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','confirmed','rejected')),
    reference_no    TEXT,
    confirmed_by    TEXT REFERENCES users(id),
    confirmed_at    TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS commissions (
    id              TEXT PRIMARY KEY,
    booking_id      TEXT NOT NULL REFERENCES bookings(id),
    payment_id      TEXT REFERENCES payments(id),
    total_amount    REAL NOT NULL,
    platform_pct    REAL DEFAULT 40.0,
    cleaner_pct     REAL DEFAULT 60.0,
    platform_share  REAL NOT NULL,
    cleaner_share   REAL NOT NULL,
    status          TEXT DEFAULT 'pending' CHECK(status IN ('pending','settled')),
    settled_at      TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    booking_id      TEXT UNIQUE NOT NULL REFERENCES bookings(id),
    customer_id     TEXT NOT NULL REFERENCES users(id),
    cleaner_id      TEXT NOT NULL REFERENCES users(id),
    rating          INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment         TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    sender_id   TEXT NOT NULL REFERENCES users(id),
    receiver_id TEXT NOT NULL REFERENCES users(id),
    booking_id  TEXT REFERENCES bookings(id),
    message_text TEXT NOT NULL,
    is_read     INTEGER DEFAULT 0,
    sent_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS disputes (
    id          TEXT PRIMARY KEY,
    booking_id  TEXT NOT NULL REFERENCES bookings(id),
    raised_by   TEXT NOT NULL REFERENCES users(id),
    description TEXT NOT NULL,
    status      TEXT DEFAULT 'open' CHECK(status IN ('open','investigating','resolved','closed')),
    resolution  TEXT,
    resolved_by TEXT REFERENCES users(id),
    created_at  TEXT DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS clock_records (
    id              TEXT PRIMARY KEY,
    cleaner_id      TEXT NOT NULL REFERENCES users(id),
    date            TEXT NOT NULL,
    clock_in        TEXT,
    clock_out       TEXT,
    approved        INTEGER DEFAULT 0,
    approved_hours  REAL,
    approved_by     TEXT REFERENCES users(id),
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(cleaner_id, date)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_bookings_customer   ON bookings(customer_id);
CREATE INDEX IF NOT EXISTS idx_bookings_cleaner    ON bookings(cleaner_id);
CREATE INDEX IF NOT EXISTS idx_bookings_status     ON bookings(status);
CREATE INDEX IF NOT EXISTS idx_messages_sender     ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_receiver   ON messages(receiver_id);
CREATE INDEX IF NOT EXISTS idx_payments_booking    ON payments(booking_id);
CREATE INDEX IF NOT EXISTS idx_commissions_booking ON commissions(booking_id);
CREATE INDEX IF NOT EXISTS idx_clock_cleaner       ON clock_records(cleaner_id);
CREATE INDEX IF NOT EXISTS idx_clock_date          ON clock_records(date);
"""


def init_db():
    """Create all tables and indexes (safe to call repeatedly)."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        conn.executescript(INDEXES)
    print(f"[DB] Initialised -> {DB_PATH}")


# ---------------------------------------------------------
#  QUERY HELPERS
# ---------------------------------------------------------

def fetchone(sql, params=()):
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def fetchall(sql, params=()):
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def execute_many(statements):
    """Run multiple (sql, params) tuples in a single transaction."""
    with get_conn() as conn:
        for sql, params in statements:
            conn.execute(sql, params)
        conn.commit()