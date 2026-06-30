"""Ads contracts + recheck aliases sau up."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from research_platform.db import connect, init_db, json_dumps, json_loads, row_to_dict

log = logging.getLogger("platform.ads")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def list_ads_contracts(*, active_only: bool = False) -> list[dict]:
    init_db()
    with connect() as conn:
        q = "SELECT * FROM ads_contracts"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY alias"
        return [dict(r) for r in conn.execute(q).fetchall()]


def upsert_ads_contract(
    alias: str,
    *,
    src_msg_ids: list[int] | None = None,
    src_chat_id: int | None = None,
    active: bool = True,
) -> dict:
    init_db()
    alias = alias.strip()
    if not alias:
        raise ValueError("alias required")
    with connect() as conn:
        row = conn.execute("SELECT * FROM ads_contracts WHERE alias=?", (alias,)).fetchone()
        ids = json_dumps(src_msg_ids or [])
        if row:
            conn.execute(
                """UPDATE ads_contracts SET src_msg_ids=?, src_chat_id=?, active=?,
                   terminated_at=?, updated_at=datetime('now') WHERE alias=?""",
                (
                    ids,
                    src_chat_id or row["src_chat_id"],
                    1 if active else 0,
                    None if active else datetime.now(VN_TZ).isoformat(),
                    alias,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO ads_contracts (alias, src_msg_ids, src_chat_id, active)
                   VALUES (?,?,?,?)""",
                (alias, ids, src_chat_id, 1 if active else 0),
            )
        return dict(conn.execute("SELECT * FROM ads_contracts WHERE alias=?", (alias,)).fetchone())


def terminate_ads_contract(alias: str) -> bool:
    init_db()
    with connect() as conn:
        cur = conn.execute(
            """UPDATE ads_contracts SET active=0, terminated_at=datetime('now')
               WHERE alias=?""",
            (alias.strip(),),
        )
        return cur.rowcount > 0


def get_contract(alias: str) -> dict | None:
    init_db()
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM ads_contracts WHERE alias=?", (alias,)).fetchone())


def resolve_ads_alias_for_msg(src_chat_id: int, msg_id: int) -> str | None:
    """Tìm alias nếu msg_id nằm trong contract."""
    for c in list_ads_contracts(active_only=True):
        ids = json_loads(c.get("src_msg_ids"), [])
        chat = c.get("src_chat_id")
        if chat and int(chat) != int(src_chat_id):
            continue
        if msg_id in ids:
            return c["alias"]
    return None


def apply_aliases_to_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        it = dict(it)
        if it.get("item_type") == "ads" and not it.get("ads_alias"):
            alias = resolve_ads_alias_for_msg(it["src_chat_id"], it["src_msg_id"])
            if alias:
                it["ads_alias"] = alias
            elif it.get("item_type") == "ads":
                it["ads_alias"] = f"auto_{it['src_msg_id']}"
        out.append(it)
    return out


def recheck_ads_aliases_for_day(day_id: int) -> dict:
    """Cập nhật day_items ads theo contracts hiện tại."""
    init_db()
    updated = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM day_items WHERE day_id=? AND item_type='ads'",
            (day_id,),
        ).fetchall()
        for r in rows:
            alias = r["ads_alias"]
            if not alias:
                continue
            c = get_contract(alias)
            if not c or not c.get("active"):
                continue
            ids = json_loads(c.get("src_msg_ids"), [])
            if ids:
                new_id = ids[0]
                conn.execute(
                    """UPDATE day_items SET src_msg_id=?, album_msg_ids=?, src_chat_id=COALESCE(?, src_chat_id)
                       WHERE id=?""",
                    (new_id, json_dumps(ids), c.get("src_chat_id"), r["id"]),
                )
                updated += 1
    return {"day_id": day_id, "updated": updated}


def recheck_all_ads_aliases() -> dict:
    init_db()
    total = 0
    with connect() as conn:
        day_ids = [r["id"] for r in conn.execute("SELECT DISTINCT day_id FROM day_items WHERE item_type='ads'")]
    for did in day_ids:
        total += recheck_ads_aliases_for_day(did)["updated"]
    return {"updated": total, "days": len(day_ids)}
