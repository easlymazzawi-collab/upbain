"""Share ref codes + leaderboard."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from research_platform.config import load_platform_config
from research_platform.db import connect, init_db
from research_platform.token_utils import generate_numeric_token

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _rand_ref(n: int = 6) -> str:
    return generate_numeric_token(n)


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
    code = generate_numeric_token(12)
    with connect() as conn:
        while conn.execute("SELECT 1 FROM share_refs WHERE ref_code=?", (code,)).fetchone():
            code = generate_numeric_token(12)
        conn.execute(
            """INSERT INTO share_refs (ref_code, day_id, bot_id, creator_user_id)
               VALUES (?,?,?,?)""",
            (code, day_id, bot_id, creator_user_id),
        )
    return code


def record_share_click(ref_code: str, user_id: int) -> bool:
    """1 user = 1 click (H34)."""
    init_db()
    ref_code = ref_code.strip()
    if ref_code.upper().startswith("REF_"):
        ref_code = ref_code[4:]
    with connect() as conn:
        row = conn.execute("SELECT ref_code FROM share_refs WHERE ref_code=?", (ref_code,)).fetchone()
        if not row:
            row = conn.execute("SELECT ref_code FROM users WHERE ref_code=?", (ref_code,)).fetchone()
        if not row:
            return False
        ref_code = row["ref_code"]
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
        code = p[4:]
        with connect() as conn:
            row = conn.execute("SELECT telegram_id, ref_code FROM users WHERE ref_code=?", (code,)).fetchone()
            if row:
                return {"type": "user_ref", "ref_code": row["ref_code"], "creator_user_id": row["telegram_id"]}
    if p.startswith("share_day_"):
        code = p[10:]
        with connect() as conn:
            row = conn.execute("SELECT * FROM share_refs WHERE ref_code=?", (code,)).fetchone()
            if row:
                return {"type": "day_ref", **dict(row)}
    if p.isdigit() and len(p) >= 8:
        with connect() as conn:
            row = conn.execute("SELECT * FROM share_refs WHERE ref_code=?", (p,)).fetchone()
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
