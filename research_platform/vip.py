"""VIP plans, kiểm tra quyền, gán tay."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from research_platform.db import connect, init_db, row_to_dict

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def list_vip_plans(*, enabled_only: bool = True) -> list[dict]:
    init_db()
    with connect() as conn:
        q = "SELECT * FROM vip_plans"
        if enabled_only:
            q += " WHERE enabled=1"
        q += " ORDER BY sort_order, id"
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_vip_plan(plan_id: int) -> dict | None:
    init_db()
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM vip_plans WHERE id=?", (plan_id,)).fetchone())


def upsert_vip_plan(
    plan_id: int | None,
    *,
    plan_type: str,
    name: str,
    stars_price: int = 0,
    duration_days: int | None = None,
    enabled: bool = True,
    sort_order: int = 0,
) -> dict:
    init_db()
    with connect() as conn:
        if plan_id:
            conn.execute(
                """UPDATE vip_plans SET plan_type=?, name=?, stars_price=?, duration_days=?,
                   enabled=?, sort_order=? WHERE id=?""",
                (plan_type, name, stars_price, duration_days, 1 if enabled else 0, sort_order, plan_id),
            )
            pid = plan_id
        else:
            cur = conn.execute(
                """INSERT INTO vip_plans (plan_type, name, stars_price, duration_days, enabled, sort_order)
                   VALUES (?,?,?,?,?,?)""",
                (plan_type, name, stars_price, duration_days, 1 if enabled else 0, sort_order),
            )
            pid = cur.lastrowid
        return dict(conn.execute("SELECT * FROM vip_plans WHERE id=?", (pid,)).fetchone())


def user_is_vip(user_id: int) -> bool:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT vip_until, tier FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if not row:
            return False
        if row["tier"] in ("vip", "admin", "lifetime"):
            if row["tier"] == "lifetime":
                return True
        until = row["vip_until"]
        if not until:
            return False
        try:
            exp = datetime.fromisoformat(until)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=VN_TZ)
            return exp > datetime.now(VN_TZ)
        except Exception:
            return False


def grant_vip(user_id: int, plan_id: int, *, source: str = "admin") -> dict:
    init_db()
    plan = get_vip_plan(plan_id)
    if not plan:
        raise ValueError("plan not found")
    now = datetime.now(VN_TZ)
    if plan["plan_type"] == "lifetime":
        until = None
        tier = "lifetime"
    else:
        days = plan.get("duration_days") or 1
        until = (now + timedelta(days=days)).isoformat()
        tier = "vip"
    with connect() as conn:
        row = conn.execute("SELECT vip_until FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        if row and row["vip_until"] and plan["plan_type"] != "lifetime":
            try:
                cur = datetime.fromisoformat(row["vip_until"])
                if cur.tzinfo is None:
                    cur = cur.replace(tzinfo=VN_TZ)
                if cur > now:
                    until = (cur + timedelta(days=plan.get("duration_days") or 1)).isoformat()
            except Exception:
                pass
        conn.execute(
            """INSERT INTO users (telegram_id, vip_until, tier) VALUES (?,?,?)
               ON CONFLICT(telegram_id) DO UPDATE SET vip_until=excluded.vip_until, tier=excluded.tier""",
            (user_id, until, tier),
        )
        conn.execute(
            """INSERT INTO purchases (user_id, plan_id, source, amount_stars)
               VALUES (?,?,?,?)""",
            (user_id, plan_id, source, plan.get("stars_price") or 0),
        )
    return {"user_id": user_id, "vip_until": until, "tier": tier}


def get_user_vip_status(user_id: int) -> dict:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT telegram_id, username, tier, vip_until, allow_forward FROM users WHERE telegram_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return {"is_vip": False, "tier": "", "vip_until": None}
        d = dict(row)
        d["is_vip"] = user_is_vip(user_id)
        return d


def can_access_archive(user_id: int, *, require_vip: bool) -> tuple[bool, str]:
    from research_platform.catalog import get_user_spam_ban

    ban = get_user_spam_ban(user_id)
    if ban:
        return False, f"Bạn bị tạm khóa đến {ban}."
    if require_vip and not user_is_vip(user_id):
        return False, "Cần VIP để xem archive. Dùng /vip hoặc ⭐ VIP / Stars."
    return True, ""
