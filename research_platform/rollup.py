"""Rollup 30 ngày — topic tổng text index."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from research_platform.archive_index import list_days, sync_bot_from_config
from research_platform.config import list_bots_config, load_platform_config
from research_platform.db import connect, init_db, json_dumps, json_loads
from research_platform.dates import topic_label_vn

log = logging.getLogger("platform.rollup")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _month_label(d: date) -> str:
    return d.strftime("%m-%Y")


def build_rollup_text(bot_id: int, month_label: str) -> tuple[str, list[int]]:
    """Tạo mục lục text cho các ngày trong tháng."""
    init_db()
    year, month = month_label.split("-")[1], month_label.split("-")[0]
    prefix = f"-{month.zfill(2) if len(month)==1 else month}-{year}"
    days = list_days(bot_id=bot_id, limit=400)
    matched = [d for d in days if d.get("topic_label", "").endswith(prefix) or month_label in d.get("date_vn", "")]
    if not matched:
        matched = days[:30]
    lines = [f"📚 Mục lục archive · {month_label}", ""]
    day_ids = []
    for d in sorted(matched, key=lambda x: x.get("date_vn", "")):
        day_ids.append(d["id"])
        with connect() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) AS c FROM day_items WHERE day_id=?",
                (d["id"],),
            ).fetchone()["c"]
        lines.append(f"• {d['topic_label']} — {cnt} bài · {d.get('status', '?')}")
    lines.append("")
    lines.append("Tra cứu qua bot: /day DD-MM-YYYY")
    return "\n".join(lines), day_ids


def save_rollup(bot_id: int, month_label: str, text: str, day_ids: list[int]) -> dict:
    init_db()
    topic_label = f"Tổng-{month_label}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO rollup_indexes (bot_id, month_label, topic_label, text_body, day_ids)
               VALUES (?,?,?,?,?)
               ON CONFLICT(bot_id, month_label) DO UPDATE SET
               text_body=excluded.text_body, day_ids=excluded.day_ids""",
            (bot_id, month_label, topic_label, text, json_dumps(day_ids)),
        )
        row = conn.execute(
            "SELECT * FROM rollup_indexes WHERE bot_id=? AND month_label=?",
            (bot_id, month_label),
        ).fetchone()
        return dict(row)


async def post_rollup_to_catalog(bot_token: str, catalog_topic_id: int, text: str) -> bool:
    from core.bot_notify import send_bot_notify_to

    if not bot_token or not catalog_topic_id:
        return False
    try:
        import json
        import urllib.request

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": catalog_topic_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
            return bool(body.get("ok"))
    except Exception as e:
        log.warning("post rollup: %s", e)
        return False


async def run_rollup_for_bot(bot_cfg: dict) -> dict | None:
    plat = load_platform_config()
    if not plat.get("enabled"):
        return None
    bot_id = sync_bot_from_config(bot_cfg)
    today = date.today()
    month_label = _month_label(today)
    text, day_ids = build_rollup_text(bot_id, month_label)
    rec = save_rollup(bot_id, month_label, text, day_ids)
    token = bot_cfg.get("token") or ""
    cat = bot_cfg.get("catalog_topic_id")
    posted = False
    if token and cat:
        posted = await post_rollup_to_catalog(token, int(cat), text)
        if posted:
            with connect() as conn:
                conn.execute(
                    "UPDATE rollup_indexes SET posted_at=datetime('now') WHERE id=?",
                    (rec["id"],),
                )
    return {"bot_id": bot_id, "month": month_label, "days": len(day_ids), "posted": posted}


async def run_rollup_all() -> list[dict]:
    results = []
    for b in list_bots_config():
        if not b.get("enabled"):
            continue
        try:
            r = await run_rollup_for_bot(b)
            if r:
                results.append(r)
        except Exception as e:
            log.exception("rollup bot %s: %s", b.get("username"), e)
    return results


def list_rollups(bot_id: int | None = None) -> list[dict]:
    init_db()
    with connect() as conn:
        if bot_id:
            rows = conn.execute(
                "SELECT * FROM rollup_indexes WHERE bot_id=? ORDER BY month_label DESC",
                (bot_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM rollup_indexes ORDER BY month_label DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["day_ids"] = json_loads(d.get("day_ids"), [])
            out.append(d)
        return out
