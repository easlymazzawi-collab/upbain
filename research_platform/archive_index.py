"""CRUD index ngày — link + metadata, không forward archive."""

from __future__ import annotations

import logging
from typing import Any

from research_platform.dates import date_vn_str, topic_label_vn, today_vn
from research_platform.db import connect, init_db, json_dumps, json_loads, row_to_dict

log = logging.getLogger("platform.archive")


def ensure_db() -> None:
    init_db()


def sync_bot_from_config(bot_cfg: dict) -> int:
    """Upsert bot row từ web config, trả bot_id."""
    ensure_db()
    src_forum = bot_cfg.get("source_forum_id")
    src_topic = bot_cfg.get("source_topic_id")
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM bots WHERE source_forum_id=? AND source_topic_id=? AND branch=?",
            (src_forum, src_topic, bot_cfg.get("branch", "ads")),
        ).fetchone()
        fields = (
            bot_cfg.get("username", ""),
            src_forum,
            src_topic,
            bot_cfg.get("catalog_topic_id"),
            bot_cfg.get("branch", "ads"),
            1 if bot_cfg.get("enabled", True) else 0,
            1 if bot_cfg.get("publish_channels", True) else 0,
            1 if bot_cfg.get("archive_index", True) else 0,
            1 if bot_cfg.get("bot_delivery", True) else 0,
            int(bot_cfg.get("queue_order") or 0),
        )
        if row:
            bot_id = row["id"]
            conn.execute(
                """UPDATE bots SET username=?, source_forum_id=?, source_topic_id=?,
                   catalog_topic_id=?, branch=?, enabled=?, publish_channels=?,
                   archive_index=?, bot_delivery=?, queue_order=? WHERE id=?""",
                (*fields, bot_id),
            )
        else:
            cur = conn.execute(
                """INSERT INTO bots (username, source_forum_id, source_topic_id,
                   catalog_topic_id, branch, enabled, publish_channels, archive_index, bot_delivery, queue_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                fields,
            )
            bot_id = cur.lastrowid
        return int(bot_id)


def find_bot_by_source(src_forum_id: int, src_topic_id: int, branch: str = "ads") -> dict | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM bots WHERE source_forum_id=? AND source_topic_id=? AND branch=? AND enabled=1",
            (src_forum_id, src_topic_id, branch),
        ).fetchone()
        return row_to_dict(row)


def get_or_create_day(bot_id: int, *, d=None) -> dict:
    ensure_db()
    d = d or today_vn()
    dv = date_vn_str(d)
    label = topic_label_vn(d)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM days WHERE bot_id=? AND date_vn=?",
            (bot_id, dv),
        ).fetchone()
        if row:
            return dict(row)
        cur = conn.execute(
            """INSERT INTO days (bot_id, date_vn, topic_label, status, channel_first)
               VALUES (?,?,?,?,1)""",
            (bot_id, dv, label, "draft"),
        )
        return dict(conn.execute("SELECT * FROM days WHERE id=?", (cur.lastrowid,)).fetchone())


def replace_day_items(
    day_id: int,
    items: list[dict[str, Any]],
    *,
    channel_sent: bool = True,
) -> int:
    """Ghi lại toàn bộ sequence cho ngày (idempotent cho cùng lượt)."""
    ensure_db()
    with connect() as conn:
        conn.execute("DELETE FROM day_items WHERE day_id=?", (day_id,))
        for i, it in enumerate(items, start=1):
            conn.execute(
                """INSERT INTO day_items
                   (day_id, seq, src_chat_id, src_msg_id, item_type, ads_alias,
                    album_msg_ids, channel_sent, indexed)
                   VALUES (?,?,?,?,?,?,?,?,1)""",
                (
                    day_id,
                    i,
                    it["src_chat_id"],
                    it["src_msg_id"],
                    it.get("item_type", "content"),
                    it.get("ads_alias"),
                    json_dumps(it.get("album_msg_ids") or [it["src_msg_id"]]),
                    1 if channel_sent else 0,
                    1,
                ),
            )
        conn.execute(
            """UPDATE days SET status='indexed', runs_count=runs_count+1,
               next_src_msg_id=? WHERE id=?""",
            (items[-1]["src_msg_id"] if items else None, day_id),
        )
    return len(items)


def publish_day(day_id: int) -> bool:
    ensure_db()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE days SET status='published' WHERE id=? AND status IN ('indexed','channel_done','draft')",
            (day_id,),
        )
        return cur.rowcount > 0


def close_day(day_id: int) -> bool:
    ensure_db()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE days SET status='closed' WHERE id=?",
            (day_id,),
        )
        return cur.rowcount > 0


def get_day_by_label(bot_id: int, topic_label: str) -> dict | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM days WHERE bot_id=? AND topic_label=?",
            (bot_id, topic_label),
        ).fetchone()
        return row_to_dict(row)


def get_day_items(day_id: int, *, active_ads_only: bool = True) -> list[dict]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM day_items WHERE day_id=? ORDER BY seq",
            (day_id,),
        ).fetchall()
        items = []
        inactive_aliases: set[str] = set()
        if active_ads_only:
            for r in conn.execute("SELECT alias FROM ads_contracts WHERE active=0"):
                inactive_aliases.add(r["alias"])
        for r in rows:
            d = dict(r)
            d["album_msg_ids"] = json_loads(d.get("album_msg_ids"), [d["src_msg_id"]])
            if d.get("item_type") == "ads" and d.get("ads_alias") in inactive_aliases:
                continue
            if d.get("item_type") == "ads" and not d.get("ads_alias"):
                # ads không alias — vẫn gửi nếu chưa revoke theo contract
                pass
            items.append(d)
        return items


def list_days(bot_id: int | None = None, limit: int = 60) -> list[dict]:
    ensure_db()
    with connect() as conn:
        if bot_id:
            rows = conn.execute(
                "SELECT * FROM days WHERE bot_id=? ORDER BY date_vn DESC LIMIT ?",
                (bot_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM days ORDER BY date_vn DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def save_delivery_progress(user_id: int, day_id: int, last_seq: int, *, completed: bool = False) -> None:
    ensure_db()
    with connect() as conn:
        conn.execute(
            """INSERT INTO user_deliveries (user_id, day_id, last_seq_sent, completed, updated_at)
               VALUES (?,?,?,?,datetime('now'))
               ON CONFLICT(user_id, day_id) DO UPDATE SET
               last_seq_sent=excluded.last_seq_sent,
               completed=excluded.completed,
               updated_at=datetime('now')""",
            (user_id, day_id, last_seq, 1 if completed else 0),
        )


def upsert_user(telegram_id: int, **fields) -> None:
    ensure_db()
    with connect() as conn:
        row = conn.execute("SELECT telegram_id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        if row:
            sets = ", ".join(f"{k}=?" for k in fields)
            conn.execute(f"UPDATE users SET {sets} WHERE telegram_id=?", (*fields.values(), telegram_id))
        else:
            cols = ["telegram_id", *fields.keys()]
            conn.execute(
                f"INSERT INTO users ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                (telegram_id, *fields.values()),
            )


def get_delivery_progress(user_id: int, day_id: int) -> dict | None:
    ensure_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_deliveries WHERE user_id=? AND day_id=?",
            (user_id, day_id),
        ).fetchone()
        return row_to_dict(row)


def sync_all_bots_from_config(plat: dict | None = None) -> list[int]:
    from research_platform.config import list_bots_config

    ids = []
    for i, b in enumerate(list_bots_config(plat)):
        b = dict(b)
        b["queue_order"] = b.get("queue_order", i)
        ids.append(sync_bot_from_config(b))
    return ids


def list_bots_db() -> list[dict]:
    ensure_db()
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM bots ORDER BY queue_order, id").fetchall()]


def get_bot_db(bot_id: int) -> dict | None:
    ensure_db()
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone())


def list_users(limit: int = 200) -> list[dict]:
    ensure_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY joined_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
