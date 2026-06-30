"""ZIP backup — port clender/utils/backup.py + UpBain platform export."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config_store import AUTO_CONFIG_FILE, DATA_DIR
from research_platform.archive_index import list_days
from research_platform.config import list_bots_config, load_platform_config
from research_platform.db import DB_PATH, connect, init_db
from research_platform.dates import date_vn_str, today_vn

log = logging.getLogger("research_platform.backup")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

BACKUP_DIR = os.path.join(DATA_DIR, "backups")
_scheduler_started = False


def _git_hash() -> str:
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _safe_copy_db(src_db: str, dst_db: str) -> bool:
    """SQLite backup API — an toàn khi DB đang ghi (clender pattern)."""
    try:
        if not os.path.isfile(src_db):
            return False
        src = sqlite3.connect(src_db)
        dst = sqlite3.connect(dst_db)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        return True
    except Exception as e:
        log.warning("safe_copy_db: %s", e)
        return False


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
            items.append({"day": dict(d), "items": [dict(r) for r in rows]})
    return {"exported_at": datetime.now(VN_TZ).isoformat(), "days": items}


def _export_bots_metadata() -> list:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, username, source_forum_id, source_topic_id, catalog_topic_id, branch, enabled FROM bots"
        ).fetchall()
        return [dict(r) for r in rows]


def _mask_config_for_export(cfg: dict) -> dict:
    cfg = json.loads(json.dumps(cfg))
    plat = cfg.get("platform") or {}
    for b in plat.get("bots") or []:
        if b.get("token"):
            b["token"] = "***"
    bot = plat.get("bot") or {}
    if bot.get("token"):
        bot["token"] = "***"
    plat["bot"] = bot
    cfg["platform"] = plat
    g = cfg.get("global") or {}
    for k in ("bot_token", "pin_bot_token", "api_hash"):
        if g.get(k):
            g[k] = "***"
    cfg["global"] = g
    return cfg


def create_backup_file() -> str | None:
    """Tạo ZIP trên disk — trả path hoặc None."""
    init_db()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now(VN_TZ).strftime("%Y-%m-%d_%H%M")
    zpath = os.path.join(BACKUP_DIR, f"backup_{ts}.zip")
    tmp_db = os.path.join(BACKUP_DIR, "_tmp_platform.db")

    archive_json = json.dumps(_export_archive_index(), ensure_ascii=False, indent=2).encode()
    bots_json = json.dumps(_export_bots_metadata(), ensure_ascii=False, indent=2).encode()
    manifest = {
        "version": 2,
        "created_at": datetime.now(VN_TZ).isoformat(),
        "git_hash": _git_hash(),
        "source": "upbain-research-platform",
    }

    has_db = _safe_copy_db(DB_PATH, tmp_db)
    try:
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            if has_db:
                zf.write(tmp_db, "platform.db")
            elif os.path.isfile(DB_PATH):
                zf.write(DB_PATH, "platform.db")
            if os.path.isfile(AUTO_CONFIG_FILE):
                with open(AUTO_CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                zf.writestr(
                    "auto_config.json",
                    json.dumps(_mask_config_for_export(cfg), ensure_ascii=False, indent=2).encode(),
                )
            zf.writestr("archive_index.json", archive_json)
            zf.writestr("bots.json", bots_json)
            manifest["checksum"] = hashlib.sha256(archive_json + bots_json).hexdigest()
            zf.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        log.info("Backup created: %s", zpath)
        return zpath
    except Exception as e:
        log.error("create_backup_file: %s", e)
        return None
    finally:
        if os.path.isfile(tmp_db):
            try:
                os.remove(tmp_db)
            except Exception:
                pass


def build_backup_zip() -> tuple[bytes, str]:
    """Tải web — đọc file mới nhất hoặc tạo mới."""
    path = create_backup_file()
    if not path:
        raise RuntimeError("Không tạo được backup")
    with open(path, "rb") as f:
        return f.read(), os.path.basename(path)


def rotate_backups() -> None:
    plat = load_platform_config()
    keep = int(plat.get("backup_keep_days") or 7)
    if not os.path.isdir(BACKUP_DIR):
        return
    cutoff = time.time() - keep * 86400
    for fname in os.listdir(BACKUP_DIR):
        if fname.startswith("backup_") and fname.endswith(".zip"):
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    log.info("Removed old backup: %s", fname)
            except Exception:
                pass


def _bot_token_for_backup() -> str:
    plat = load_platform_config()
    bots = list_bots_config(plat)
    for b in bots:
        if b.get("token"):
            return b["token"]
    from core.settings import bot_token
    return bot_token() or ""


async def _ensure_backup_topic(bot_token: str, forum_id: int, topic_id: int | None) -> int | None:
    if topic_id:
        return int(topic_id)
    import json
    import urllib.request

    url = f"https://api.telegram.org/bot{bot_token}/createForumTopic"
    payload = {"chat_id": forum_id, "name": "backup"}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode())
        if body.get("ok"):
            tid = body["result"]["message_thread_id"]
            plat = load_platform_config()
            plat["admin_zip_topic_id"] = tid
            from research_platform.config import save_platform_config
            save_platform_config(plat)
            return tid
    except Exception as e:
        log.warning("createForumTopic backup: %s", e)
    return None


async def send_backup_to_telegram(zip_path: str) -> bool:
    """Gửi ZIP lên forum admin — port clender send_backup_to_telegram."""
    plat = load_platform_config()
    token = _bot_token_for_backup()
    forum_id = plat.get("backup_forum_id") or plat.get("admin_forum_id")
    topic_id = plat.get("admin_zip_topic_id")
    if not token or not forum_id:
        log.warning("send_backup_to_telegram: thiếu token hoặc backup_forum_id/admin_forum_id")
        return False
    if not zip_path or not os.path.isfile(zip_path):
        return False

    tid = await _ensure_backup_topic(token, int(forum_id), topic_id)
    cap = f"📦 UpBain backup {datetime.now(VN_TZ).strftime('%d/%m/%Y %H:%M')}"

    from aiogram import Bot
    from aiogram.types import FSInputFile

    bot = Bot(token=token)
    try:
        doc = FSInputFile(zip_path, filename=os.path.basename(zip_path))
        kwargs: dict = {"chat_id": int(forum_id), "document": doc, "caption": cap}
        if tid:
            kwargs["message_thread_id"] = int(tid)
        await bot.send_document(**kwargs)
        log.info("Backup sent to Telegram forum %s topic %s", forum_id, tid)
        return True
    except Exception as e:
        log.error("send_backup_to_telegram: %s", e)
        return False
    finally:
        await bot.session.close()


def get_backup_status() -> dict:
    plat = load_platform_config()
    to_tg = bool(plat.get("backup_to_telegram", True))
    files = []
    if os.path.isdir(BACKUP_DIR):
        files = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".zip")],
            reverse=True,
        )
    last_file = files[0] if files else None
    last_mtime = None
    if last_file:
        last_mtime = datetime.fromtimestamp(
            os.path.getmtime(os.path.join(BACKUP_DIR, last_file)), tz=VN_TZ,
        ).isoformat()
    forum_id = plat.get("backup_forum_id") or plat.get("admin_forum_id")
    token = _bot_token_for_backup()
    reasons = []
    if not to_tg:
        reasons.append("backup_to_telegram=false — chỉ lưu local")
    if to_tg and not token:
        reasons.append("Thiếu bot token")
    if to_tg and not forum_id:
        reasons.append("Thiếu backup_forum_id hoặc admin_forum_id")
    return {
        "scheduler_on": _scheduler_started,
        "interval_hours": int(plat.get("backup_interval_hours") or 24),
        "keep_days": int(plat.get("backup_keep_days") or 7),
        "backup_dir": BACKUP_DIR,
        "telegram_enabled": to_tg,
        "telegram_ready": to_tg and bool(token and forum_id),
        "local_file_count": len(files),
        "last_backup_file": last_file,
        "last_backup_at": last_mtime,
        "db_exists": os.path.isfile(DB_PATH),
        "skip_reasons": reasons,
    }


def _backup_loop() -> None:
    time.sleep(60)
    while True:
        try:
            plat = load_platform_config()
            if not plat.get("enabled"):
                time.sleep(3600)
                continue
            path = create_backup_file()
            rotate_backups()
            if plat.get("backup_to_telegram", True) and path:
                import asyncio
                asyncio.run(send_backup_to_telegram(path))
        except Exception as e:
            log.error("backup_loop: %s", e)
        hours = int(load_platform_config().get("backup_interval_hours") or 24)
        time.sleep(max(1, hours) * 3600)


def start_backup_scheduler() -> bool:
    global _scheduler_started
    if _scheduler_started:
        return False
    _scheduler_started = True
    t = threading.Thread(target=_backup_loop, daemon=True, name="platform-backup")
    t.start()
    log.info("Platform backup scheduler started")
    return True


def run_backup_now(send_telegram: bool | None = None) -> dict:
    path = create_backup_file()
    rotate_backups()
    if send_telegram is None:
        send_telegram = bool(load_platform_config().get("backup_to_telegram", True))
    sent = False
    if send_telegram and path:
        import asyncio
        sent = asyncio.run(send_backup_to_telegram(path))
    return {
        "ok": bool(path),
        "path": path,
        "sent_telegram": sent,
        "status": get_backup_status(),
    }
