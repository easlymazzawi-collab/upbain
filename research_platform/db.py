"""SQLite schema cho Research Platform."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from core.config_store import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "platform.db")
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT DEFAULT '',
    source_forum_id INTEGER,
    source_topic_id INTEGER,
    catalog_topic_id INTEGER,
    branch TEXT DEFAULT 'ads',
    enabled INTEGER DEFAULT 1,
    publish_channels INTEGER DEFAULT 1,
    archive_index INTEGER DEFAULT 1,
    bot_delivery INTEGER DEFAULT 1,
    queue_order INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    date_vn TEXT NOT NULL,
    topic_label TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    runs_count INTEGER DEFAULT 0,
    channel_first INTEGER DEFAULT 1,
    next_src_msg_id INTEGER,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(bot_id, date_vn),
    FOREIGN KEY (bot_id) REFERENCES bots(id)
);

CREATE TABLE IF NOT EXISTS day_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    src_chat_id INTEGER NOT NULL,
    src_msg_id INTEGER NOT NULL,
    item_type TEXT DEFAULT 'content',
    ads_alias TEXT,
    album_msg_ids TEXT DEFAULT '[]',
    channel_sent INTEGER DEFAULT 0,
    indexed INTEGER DEFAULT 0,
    UNIQUE(day_id, seq),
    FOREIGN KEY (day_id) REFERENCES days(id)
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT DEFAULT '',
    first_name TEXT DEFAULT '',
    joined_at TEXT DEFAULT (datetime('now')),
    language_code TEXT DEFAULT '',
    tier TEXT DEFAULT '',
    vip_until TEXT,
    spam_ban_until TEXT,
    allow_forward INTEGER DEFAULT 1,
    ref_code TEXT
);

CREATE TABLE IF NOT EXISTS user_deliveries (
    user_id INTEGER NOT NULL,
    day_id INTEGER NOT NULL,
    last_seq_sent INTEGER DEFAULT 0,
    completed INTEGER DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, day_id),
    FOREIGN KEY (day_id) REFERENCES days(id)
);

CREATE TABLE IF NOT EXISTS ads_contracts (
    alias TEXT PRIMARY KEY,
    src_msg_ids TEXT DEFAULT '[]',
    src_chat_id INTEGER,
    active INTEGER DEFAULT 1,
    terminated_at TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vip_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_type TEXT NOT NULL,
    name TEXT NOT NULL,
    stars_price INTEGER DEFAULT 0,
    duration_days INTEGER,
    enabled INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gift_codes (
    code TEXT PRIMARY KEY,
    plan_id INTEGER,
    max_uses INTEGER DEFAULT 1,
    used_count INTEGER DEFAULT 0,
    expires_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (plan_id) REFERENCES vip_plans(id)
);

CREATE TABLE IF NOT EXISTS gift_redeems (
    code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    redeemed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (code, user_id)
);

CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plan_id INTEGER,
    source TEXT DEFAULT 'stars',
    amount_stars INTEGER DEFAULT 0,
    gift_code TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS media_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    day_id INTEGER,
    item_ids TEXT DEFAULT '[]',
    status TEXT DEFAULT 'approved',
    contributor_user_id INTEGER,
    approved_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (day_id) REFERENCES days(id)
);

CREATE TABLE IF NOT EXISTS contribution_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER,
    user_id INTEGER NOT NULL,
    src_chat_id INTEGER,
    msg_ids TEXT DEFAULT '[]',
    proposed_name TEXT,
    status TEXT DEFAULT 'pending',
    admin_msg_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS share_refs (
    ref_code TEXT PRIMARY KEY,
    day_id INTEGER,
    bot_id INTEGER,
    creator_user_id INTEGER,
    click_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS share_clicks (
    ref_code TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    clicked_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (ref_code, user_id)
);

CREATE TABLE IF NOT EXISTS rollup_indexes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id INTEGER NOT NULL,
    month_label TEXT NOT NULL,
    topic_label TEXT,
    text_body TEXT,
    day_ids TEXT DEFAULT '[]',
    posted_at TEXT,
    UNIQUE(bot_id, month_label)
);

CREATE INDEX IF NOT EXISTS idx_days_bot_date ON days(bot_id, date_vn);
CREATE INDEX IF NOT EXISTS idx_day_items_day ON day_items(day_id, seq);
CREATE INDEX IF NOT EXISTS idx_media_sets_norm ON media_sets(name_norm);
CREATE INDEX IF NOT EXISTS idx_users_ref ON users(ref_code);
"""

_MIGRATIONS = [
    "ALTER TABLE bots ADD COLUMN queue_order INTEGER DEFAULT 0",
    "ALTER TABLE ads_contracts ADD COLUMN src_chat_id INTEGER",
    "ALTER TABLE ads_contracts ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))",
    "ALTER TABLE users ADD COLUMN ref_code TEXT",
]


def _ensure_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    _ensure_dir()
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass


def _seed_vip_plans(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT COUNT(*) AS c FROM vip_plans").fetchone()
    if row and row["c"] > 0:
        return
    defaults = [
        ("day", "VIP 1 ngày", 50, 1, 1),
        ("month", "VIP 1 tháng", 500, 30, 2),
        ("year", "VIP 1 năm", 4000, 365, 3),
        ("lifetime", "VIP vĩnh viễn", 10000, None, 4),
    ]
    for pt, name, stars, days, order in defaults:
        conn.execute(
            """INSERT INTO vip_plans (plan_type, name, stars_price, duration_days, enabled, sort_order)
               VALUES (?,?,?,?,1,?)""",
            (pt, name, stars, days, order),
        )


def init_db() -> None:
    with _lock:
        with connect() as conn:
            conn.executescript(_SCHEMA)
            _run_migrations(conn)
            _seed_vip_plans(conn)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def json_loads(text: str | None, default=None):
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except Exception:
        return default if default is not None else []


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
