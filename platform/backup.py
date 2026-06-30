"""ZIP backup — platform.db + export JSON."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config_store import AUTO_CONFIG_FILE, DATA_DIR
from platform.archive_index import list_days
from platform.db import DB_PATH, connect, init_db
from platform.dates import date_vn_str, today_vn

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _git_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _export_archive_index() -> dict:
    init_db()
    days = list_days(limit=500)
    with connect() as conn:
        items = []
        for d in days:
            rows = conn.execute(
                "SELECT * FROM day_items WHERE day_id=? ORDER BY seq",
                (d["id"],),
            ).fetchall()
            items.append({
                "day": dict(d),
                "items": [dict(r) for r in rows],
            })
    return {"exported_at": datetime.now(VN_TZ).isoformat(), "days": items}


def _export_bots_metadata() -> list:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, username, source_forum_id, source_topic_id, catalog_topic_id, branch, enabled FROM bots"
        ).fetchall()
        return [dict(r) for r in rows]


def build_backup_zip() -> tuple[bytes, str]:
    """Tạo ZIP backup, trả (bytes, filename)."""
    init_db()
    stamp = date_vn_str(today_vn())
    filename = f"backup_{stamp}.zip"
    buf = io.BytesIO()

    archive_json = json.dumps(_export_archive_index(), ensure_ascii=False, indent=2).encode()
    bots_json = json.dumps(_export_bots_metadata(), ensure_ascii=False, indent=2).encode()

    manifest = {
        "version": 1,
        "created_at": datetime.now(VN_TZ).isoformat(),
        "git_hash": _git_hash(),
        "files": {},
    }

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if os.path.isfile(DB_PATH):
            zf.write(DB_PATH, "platform.db")
            manifest["files"]["platform.db"] = _file_sha256(DB_PATH)
        if os.path.isfile(AUTO_CONFIG_FILE):
            # Strip delivery bot token from export copy
            with open(AUTO_CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            plat = cfg.get("platform") or {}
            bot = plat.get("bot") or {}
            if bot.get("token"):
                bot = {**bot, "token": "***"}
                plat = {**plat, "bot": bot}
                cfg = {**cfg, "platform": plat}
            cfg_bytes = json.dumps(cfg, ensure_ascii=False, indent=2).encode()
            zf.writestr("auto_config.json", cfg_bytes)
        zf.writestr("archive_index.json", archive_json)
        zf.writestr("bots.json", bots_json)
        manifest["checksum"] = hashlib.sha256(archive_json + bots_json).hexdigest()
        zf.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return buf.getvalue(), filename


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
