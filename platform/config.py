"""Cấu hình Research Platform — lưu trong auto_config.json."""

from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

from core.config_store import load_auto_config, save_auto_config

_lock = threading.Lock()

DEFAULT_PLATFORM: dict[str, Any] = {
    "enabled": False,
    "publish_channels": True,
    "archive_index": True,
    "bot_delivery": True,
    "channel_first": True,
    "delivery_delay_sec": 1.0,
    "admin_forum_id": None,
    "admin_zip_topic_id": None,
    "admin_notify_group_id": None,
    "membership_channel_id": None,
    "backup_forum_id": None,
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
}


def load_platform_config() -> dict[str, Any]:
    with _lock:
        cfg = load_auto_config()
        plat = cfg.setdefault("platform", deepcopy(DEFAULT_PLATFORM))
        for k, v in DEFAULT_PLATFORM.items():
            if k == "bot":
                bot = plat.setdefault("bot", deepcopy(DEFAULT_PLATFORM["bot"]))
                for bk, bv in DEFAULT_PLATFORM["bot"].items():
                    bot.setdefault(bk, bv)
            else:
                plat.setdefault(k, deepcopy(v) if isinstance(v, (dict, list)) else v)
        return plat


def save_platform_config(plat: dict[str, Any]) -> None:
    with _lock:
        cfg = load_auto_config()
        cfg["platform"] = plat
        save_auto_config(cfg)


def platform_enabled() -> bool:
    return bool(load_platform_config().get("enabled"))


def layer_enabled(layer: str, bot_cfg: dict | None = None) -> bool:
    plat = load_platform_config()
    if not plat.get("enabled"):
        return False
    if bot_cfg is None:
        bot_cfg = plat.get("bot") or {}
    if layer in ("publish_channels", "archive_index", "bot_delivery"):
        return bool(plat.get(layer, True) and bot_cfg.get(layer, True))
    return bool(plat.get(layer, True))
