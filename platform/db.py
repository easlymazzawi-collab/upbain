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
    allow_forward INTEGER DEFAULT 1
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
    active INTEGER DEFAULT 1,
    terminated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_days_bot_date ON days(bot_id, date_vn);
CREATE INDEX IF NOT EXISTS idx_day_items_day ON day_items(day_id, seq);
"""


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


def init_db() -> None:
    with _lock:
        with connect() as conn:
            conn.executescript(_SCHEMA)


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
