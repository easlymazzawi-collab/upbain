"""Persistent config: topic sources, /all task, global settings."""

import json
import os
import threading
from copy import deepcopy
from typing import Any

DATA_DIR = os.getenv("DATA_DIR", "data")
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
        "notify_chat_id": None,
        "xepbai_off_default_cpa": 1,
        "xepbai_off_default_mode": "normal",
        "low_media_warn_threshold": 50,
        "web_host": "0.0.0.0",
        "web_port": 8080,
        "web_token": "",
        "auto_run_enabled": True,
        "schedule": {
            "enabled": False,
            "times": ["08:00", "20:00"],
            "timezone": "Asia/Ho_Chi_Minh",
            "run_all_topics": True,
            "run_all_task_after": True,
        },
    },
    "topic_sources": {},
    "all_task": {
        "enabled": False,
        "source_chat_id": None,
        "source_topic_id": None,
        "source_title": "",
        "selected_channel_ids": [],
        "run_after_regular": True,
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


def get_topic_source(src_chat_id: int, topic_id: int) -> dict | None:
    cfg = load_auto_config()
    return cfg.get("topic_sources", {}).get(topic_key(src_chat_id, topic_id))


def upsert_topic_source(src_chat_id: int, topic_id: int, data: dict) -> dict:
    cfg = load_auto_config()
    key = topic_key(src_chat_id, topic_id)
    entry = cfg.setdefault("topic_sources", {}).get(key, {})
    entry.update(data)
    entry["src_chat_id"] = src_chat_id
    entry["topic_id"] = topic_id
    cfg["topic_sources"][key] = entry
    save_auto_config(cfg)
    return entry


def list_topic_sources() -> list[dict]:
    cfg = load_auto_config()
    return list(cfg.get("topic_sources", {}).values())
