"""Media sets — /find, đóng góp tên, spam ban."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research_platform.db import connect, init_db, json_dumps, json_loads

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def normalize_name(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def search_media_sets(query: str, *, bot_id: int | None = None, limit: int = 20) -> list[dict]:
    init_db()
    norm = normalize_name(query)
    if not norm:
        return []
    with connect() as conn:
        if bot_id:
            rows = conn.execute(
                """SELECT * FROM media_sets WHERE status='approved' AND bot_id=?
                   AND name_norm LIKE ? ORDER BY created_at DESC LIMIT ?""",
                (bot_id, f"%{norm}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM media_sets WHERE status='approved' AND name_norm LIKE ?
                   ORDER BY created_at DESC LIMIT ?""",
                (f"%{norm}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]


def approve_media_set(set_id: int, *, approved_by: str = "admin") -> dict | None:
    init_db()
    with connect() as conn:
        conn.execute(
            "UPDATE media_sets SET status='approved', approved_by=? WHERE id=?",
            (approved_by, set_id),
        )
        row = conn.execute("SELECT * FROM media_sets WHERE id=?", (set_id,)).fetchone()
        return dict(row) if row else None


def create_media_set(
    *,
    bot_id: int,
    name: str,
    day_id: int | None = None,
    item_ids: list | None = None,
    contributor_user_id: int | None = None,
    status: str = "approved",
) -> dict:
    init_db()
    norm = normalize_name(name)
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO media_sets (bot_id, name, name_norm, day_id, item_ids, status, contributor_user_id)
               VALUES (?,?,?,?,?,?,?)""",
            (bot_id, name.strip(), norm, day_id, json_dumps(item_ids or []), status, contributor_user_id),
        )
        return dict(conn.execute("SELECT * FROM media_sets WHERE id=?", (cur.lastrowid,)).fetchone())


def queue_contribution(
    *,
    bot_id: int,
    user_id: int,
    src_chat_id: int,
    msg_ids: list[int],
    proposed_name: str,
) -> int:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO contribution_queue (bot_id, user_id, src_chat_id, msg_ids, proposed_name)
               VALUES (?,?,?,?,?)""",
            (bot_id, user_id, src_chat_id, json_dumps(msg_ids), proposed_name.strip()),
        )
        return int(cur.lastrowid)


def list_pending_contributions(limit: int = 50) -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM contribution_queue WHERE status='pending'
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["msg_ids"] = json_loads(d.get("msg_ids"), [])
            out.append(d)
        return out


def approve_contribution(contrib_id: int, *, approved_by: str = "admin") -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM contribution_queue WHERE id=?", (contrib_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE contribution_queue SET status='approved' WHERE id=?", (contrib_id,))
        ms = create_media_set(
            bot_id=row["bot_id"],
            name=row["proposed_name"],
            contributor_user_id=row["user_id"],
            status="approved",
        )
        ms["approved_by"] = approved_by
        return ms


def reject_contribution(contrib_id: int) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE contribution_queue SET status='rejected' WHERE id=?",
            (contrib_id,),
        )
        return cur.rowcount > 0


def apply_spam_ban(user_id: int, *, hours: int = 24, escalate: bool = False) -> str:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT spam_ban_until FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        h = hours
        if escalate and row and row["spam_ban_until"]:
            h = 36
        until = (datetime.now(VN_TZ) + timedelta(hours=h)).isoformat()
        conn.execute(
            """INSERT INTO users (telegram_id, spam_ban_until) VALUES (?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET spam_ban_until=excluded.spam_ban_until""",
            (user_id, until),
        )
        return until


def get_user_spam_ban(user_id: int) -> str | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT spam_ban_until FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if not row or not row["spam_ban_until"]:
            return None
        try:
            exp = datetime.fromisoformat(row["spam_ban_until"])
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=VN_TZ)
            if exp > datetime.now(VN_TZ):
                return row["spam_ban_until"]
        except Exception:
            pass
        return None


def is_spam_contribution(user_id: int, name: str, *, rate_limit_sec: int = 30) -> bool:
    """Duplicate / rate limit đơn giản."""
    init_db()
    norm = normalize_name(name)
    with connect() as conn:
        recent = conn.execute(
            """SELECT created_at FROM contribution_queue
               WHERE user_id=? ORDER BY created_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        if recent:
            try:
                t = datetime.fromisoformat(recent["created_at"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=VN_TZ)
                if (datetime.now(VN_TZ) - t).total_seconds() < rate_limit_sec:
                    return True
            except Exception:
                pass
        dup = conn.execute(
            """SELECT 1 FROM media_sets WHERE contributor_user_id=? AND name_norm=?""",
            (user_id, norm),
        ).fetchone()
        return bool(dup)
