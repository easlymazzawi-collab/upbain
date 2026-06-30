"""Giftcode tạo + redeem."""

from __future__ import annotations

import secrets
import string
from datetime import datetime
from zoneinfo import ZoneInfo

from research_platform.db import connect, init_db
from research_platform.vip import grant_vip

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _gen_code(prefix: str = "", length: int = 8) -> str:
    chars = string.ascii_uppercase + string.digits
    body = "".join(secrets.choice(chars) for _ in range(length))
    return f"{prefix}{body}" if prefix else body


def create_gift_codes(
    *,
    plan_id: int,
    count: int = 1,
    prefix: str = "",
    max_uses: int = 1,
    expires_at: str | None = None,
    custom_code: str | None = None,
) -> list[str]:
    init_db()
    codes = []
    with connect() as conn:
        for _ in range(max(1, count)):
            code = custom_code or _gen_code(prefix)
            custom_code = None
            conn.execute(
                """INSERT OR IGNORE INTO gift_codes (code, plan_id, max_uses, expires_at)
                   VALUES (?,?,?,?)""",
                (code.upper(), plan_id, max_uses, expires_at),
            )
            codes.append(code.upper())
    return codes


def list_gift_codes(limit: int = 100) -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """SELECT g.*, p.name AS plan_name FROM gift_codes g
               LEFT JOIN vip_plans p ON p.id=g.plan_id
               ORDER BY g.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def redeem_gift_code(user_id: int, code: str) -> dict:
    init_db()
    code = code.strip().upper()
    with connect() as conn:
        row = conn.execute("SELECT * FROM gift_codes WHERE code=?", (code,)).fetchone()
        if not row:
            raise ValueError("Mã không tồn tại")
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"])
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=VN_TZ)
                if exp < datetime.now(VN_TZ):
                    raise ValueError("Mã đã hết hạn")
            except ValueError as e:
                if "Mã" in str(e):
                    raise
        if row["used_count"] >= row["max_uses"]:
            raise ValueError("Mã đã hết lượt")
        redeemed = conn.execute(
            "SELECT 1 FROM gift_redeems WHERE code=? AND user_id=?",
            (code, user_id),
        ).fetchone()
        if redeemed:
            raise ValueError("Bạn đã dùng mã này")
    result = grant_vip(user_id, row["plan_id"], source="giftcode")
    with connect() as conn:
        conn.execute(
            "UPDATE gift_codes SET used_count=used_count+1 WHERE code=?",
            (code,),
        )
        conn.execute(
            "INSERT INTO gift_redeems (code, user_id) VALUES (?,?)",
            (code, user_id),
        )
        conn.execute(
            """INSERT INTO purchases (user_id, plan_id, source, gift_code)
               VALUES (?,?,?,?)""",
            (user_id, row["plan_id"], "giftcode", code),
        )
    return {"ok": True, "code": code, **result}
