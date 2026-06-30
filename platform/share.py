"""Share ref codes + leaderboard."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from zoneinfo import ZoneInfo

from platform.config import load_platform_config
from platform.db import connect, init_db, row_to_dict

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _rand_ref(n: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(n))


def ensure_user_ref_code(user_id: int) -> str:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT ref_code FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if row and row["ref_code"]:
            return row["ref_code"]
        code = _rand_ref()
        while conn.execute("SELECT 1 FROM users WHERE ref_code=?", (code,)).fetchone():
            code = _rand_ref()
        conn.execute(
            """INSERT INTO users (telegram_id, ref_code) VALUES (?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET ref_code=excluded.ref_code""",
            (user_id, code),
        )
        return code


def create_day_share_ref(day_id: int, bot_id: int, creator_user_id: int) -> str:
    init_db()
    code = f"DAY{_rand_ref(5)}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO share_refs (ref_code, day_id, bot_id, creator_user_id)
               VALUES (?,?,?,?)""",
            (code, day_id, bot_id, creator_user_id),
        )
    return code


def record_share_click(ref_code: str, user_id: int) -> bool:
    """1 user = 1 click (H34)."""
    init_db()
    ref_code = ref_code.strip().upper()
    if ref_code.startswith("REF_"):
        ref_code = ref_code[4:]
    with connect() as conn:
        if ref_code.startswith("DAY"):
            row = conn.execute("SELECT ref_code FROM share_refs WHERE ref_code=?", (ref_code,)).fetchone()
        else:
            row = conn.execute("SELECT ref_code FROM users WHERE ref_code=?", (ref_code,)).fetchone()
            if row:
                ref_code = row["ref_code"]
        if not row:
            return False
        exists = conn.execute(
            "SELECT 1 FROM share_clicks WHERE ref_code=? AND user_id=?",
            (ref_code, user_id),
        ).fetchone()
        if exists:
            return False
        conn.execute(
            "INSERT INTO share_clicks (ref_code, user_id) VALUES (?,?)",
            (ref_code, user_id),
        )
        conn.execute(
            "UPDATE share_refs SET click_count=click_count+1 WHERE ref_code=?",
            (ref_code,),
        )
        return True


def resolve_ref_payload(payload: str) -> dict | None:
    init_db()
    p = payload.strip()
    if p.startswith("ref_"):
        code = p[4:].upper()
        with connect() as conn:
            row = conn.execute("SELECT telegram_id, ref_code FROM users WHERE ref_code=?", (code,)).fetchone()
            if row:
                return {"type": "user_ref", "ref_code": row["ref_code"], "creator_user_id": row["telegram_id"]}
    if p.startswith("share_day_"):
        code = p[10:].upper()
        with connect() as conn:
            row = conn.execute("SELECT * FROM share_refs WHERE ref_code=?", (code,)).fetchone()
            if row:
                return {"type": "day_ref", **dict(row)}
    return None


def leaderboard(limit: int = 10) -> list[dict]:
    init_db()
    plat = load_platform_config()
    if not plat.get("share_event", {}).get("enabled"):
        return []
    with connect() as conn:
        rows = conn.execute(
            """SELECT u.telegram_id, u.username, u.first_name, u.ref_code,
                      COUNT(sc.user_id) AS clicks
               FROM users u
               LEFT JOIN share_clicks sc ON sc.ref_code=u.ref_code
               WHERE u.ref_code IS NOT NULL AND u.ref_code != ''
               GROUP BY u.telegram_id
               ORDER BY clicks DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        out = []
        for i, r in enumerate(rows, 1):
            d = dict(r)
            d["rank"] = i
            un = d.get("username") or ""
            if un:
                d["display"] = f"@{un[:3]}***" if len(un) > 3 else f"@{un}"
            else:
                d["display"] = (d.get("first_name") or "User")[:1] + "***"
            out.append(d)
        return out
