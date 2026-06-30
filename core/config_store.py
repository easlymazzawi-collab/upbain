"""Persistent config: topic sources, /all task, global settings."""

import json
import os
import threading
from copy import deepcopy
from typing import Any

DATA_DIR = "data"
AUTO_CONFIG_FILE = os.path.join(DATA_DIR, "auto_config.json")
INVENTORY_FILE = os.path.join(DATA_DIR, "inventory.json")

_lock = threading.Lock()

DEFAULT_AUTO_CONFIG: dict[str, Any] = {
    "global": {
        "api_id": None,
        "api_hash": "",
        "intermediate_chat": None,
        "ads_chat": None,
        "bot_token": "",
        "pin_bot_token": "",
        "notify_chat_id": None,
        "xepbai_off_default_cpa": 1,
        "xepbai_off_default_mode": "normal",
        "xepbai_mode": "off",
        "xepbai_whitelist_cmds": [],
        "low_media_warn_threshold": 50,
        "web_host": "0.0.0.0",
        "web_port": 8080,
        "web_token": "",
        "auto_stage_via_saved": True,
        "auto_run_enabled": True,
        "system_armed": False,
        "require_full_batch": True,
        "require_up_confirm": True,
        "confirm_cancel_before_sec": 900,
        "stock_poll_interval_sec": 300,
        "schedule": {
            "enabled": False,
            "times": ["08:00", "20:00"],
            "timezone": "Asia/Ho_Chi_Minh",
            "run_all_topics": True,
            "run_all_task_after": True,
        },
    },
    "topic_sources": {},
    "plain_topic_sources": {},
    "all_task": {
        "enabled": False,
        "source_chat_id": None,
        "source_topic_id": None,
        "source_title": "/all",
        "selected_channel_ids": [],
        "run_after_regular": False,
        "use_ads": False,
        "xep_cpa": 1,
        "xep_mode": "normal",
        "target_media_override": None,
        "start_link": "",
        "start_msg_id": None,
        "pin_mode": "latest",
        "cursor_msg_id": None,
        "include_text_posts": True,
        "require_full_batch": False,
        "require_up_confirm": False,
    },
    "plain_task": {
        "enabled": False,
        "selected_channel_ids": [],
        "skip_last_posts": 0,
        "included_msg_ids": None,
        "batch_signature": "",
    },
        "platform": {
            "enabled": False,
            "publish_channels": True,
            "archive_index": True,
            "bot_delivery": True,
            "channel_first": True,
            "delivery_delay_sec": 1.0,
            "orchestrator_running": True,
            "require_vip_for_archive": False,
            "admin_forum_id": None,
            "admin_zip_topic_id": None,
            "admin_contrib_topic_id": None,
            "admin_notify_group_id": None,
            "membership_channel_id": None,
            "membership_channel_username": "",
            "force_join_check_sec": 300,
            "force_join_message": (
                "🔒 Bạn cần tham gia kênh để dùng bot.\n"
                "Nhấn Join → bấm ✅ Kiểm tra lại."
            ),
            "backup_forum_id": None,
            "backup_to_telegram": True,
            "backup_interval_hours": 24,
            "backup_keep_days": 7,
            "share_event": {"enabled": False, "period_start": None, "period_end": None},
            "bots": [],
            "bot": {
            "token": "",
            "username": "",
            "source_forum_id": None,
            "source_topic_id": None,
            "catalog_topic_id": None,
            "branch": "ads",
            "enabled": True,
            "publish_channels": True,
            "archive_index": True,
            "bot_delivery": True,
        },
    },
}

DEFAULT_INVENTORY: dict[str, Any] = {"topics": {}, "updated_at": 0}


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def _read_json(path: str, default: dict) -> dict:
    _ensure_data_dir()
    if not os.path.exists(path):
        return deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return deepcopy(default)


def _write_json(path: str, data: dict) -> None:
    _ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def topic_key(src_chat_id: int, topic_id: int) -> str:
    return f"{src_chat_id}:{topic_id}"


def load_auto_config() -> dict:
    with _lock:
        cfg = _read_json(AUTO_CONFIG_FILE, DEFAULT_AUTO_CONFIG)
        for k, v in DEFAULT_AUTO_CONFIG.items():
            cfg.setdefault(k, deepcopy(v))
        cfg["global"].setdefault("web_port", 8080)
        return cfg


def save_auto_config(cfg: dict) -> None:
    with _lock:
        _write_json(AUTO_CONFIG_FILE, cfg)


def load_inventory() -> dict:
    with _lock:
        inv = _read_json(INVENTORY_FILE, DEFAULT_INVENTORY)
        inv.setdefault("topics", {})
        return inv


def save_inventory(inv: dict) -> None:
    import time
    inv["updated_at"] = int(time.time())
    with _lock:
        _write_json(INVENTORY_FILE, inv)


def get_topic_source(src_chat_id: int, topic_id: int, branch: str | None = None) -> dict | None:
    from core.branch_map import sources_key
    from core.channel_store import BRANCH_ADS

    cfg = load_auto_config()
    b = branch or BRANCH_ADS
    return cfg.get(sources_key(b), {}).get(topic_key(src_chat_id, topic_id))


def upsert_topic_source(
    src_chat_id: int,
    topic_id: int,
    data: dict,
    branch: str | None = None,
) -> dict:
    from core.branch_map import sources_key
    from core.channel_store import BRANCH_ADS

    b = branch or BRANCH_ADS
    cfg = load_auto_config()
    key = topic_key(src_chat_id, topic_id)
    sk = sources_key(b)
    entry = cfg.setdefault(sk, {}).get(key, {})
    entry.update(data)
    entry["src_chat_id"] = src_chat_id
    entry["topic_id"] = topic_id
    if b == "plain":
        entry["use_ads"] = False
    cfg[sk][key] = entry
    save_auto_config(cfg)
    return entry


def list_topic_sources(branch: str | None = None) -> list[dict]:
    from core.branch_map import sources_key
    from core.channel_store import BRANCH_ADS

    cfg = load_auto_config()
    return list(cfg.get(sources_key(branch or BRANCH_ADS), {}).values())
