"""Cấu hình Research Platform — lưu trong auto_config.json."""

from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any

from core.config_store import load_auto_config, save_auto_config

_lock = threading.Lock()

_DEFAULT_BOT: dict[str, Any] = {
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
    "queue_order": 0,
}

DEFAULT_PLATFORM: dict[str, Any] = {
    "enabled": False,
    "publish_channels": True,
    "archive_index": True,
    "bot_delivery": True,
    "channel_first": True,
    "delivery_delay_sec": 1.0,
    "orchestrator_running": True,
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
    "require_vip_for_archive": False,
    "share_event": {
        "enabled": False,
        "period_start": None,
        "period_end": None,
    },
    "bot": deepcopy(_DEFAULT_BOT),
    "bots": [],
}


def _normalize_bot(bot: dict) -> dict:
    out = {**_DEFAULT_BOT, **(bot or {})}
    return out


def list_bots_config(plat: dict | None = None) -> list[dict]:
    plat = plat or load_platform_config()
    bots = plat.get("bots") or []
    if bots:
        return [_normalize_bot(b) for b in bots]
    legacy = plat.get("bot") or {}
    if legacy.get("token") or legacy.get("source_forum_id"):
        return [_normalize_bot(legacy)]
    return []


def load_platform_config() -> dict[str, Any]:
    with _lock:
        cfg = load_auto_config()
        plat = cfg.setdefault("platform", deepcopy(DEFAULT_PLATFORM))
        for k, v in DEFAULT_PLATFORM.items():
            if k in ("bot", "bots", "share_event"):
                continue
            plat.setdefault(k, deepcopy(v) if isinstance(v, (dict, list)) else v)
        plat.setdefault("bot", deepcopy(_DEFAULT_BOT))
        plat.setdefault("bots", [])
        se = plat.setdefault("share_event", deepcopy(DEFAULT_PLATFORM["share_event"]))
        for sk, sv in DEFAULT_PLATFORM["share_event"].items():
            se.setdefault(sk, sv)
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
        bots = list_bots_config(plat)
        bot_cfg = bots[0] if bots else plat.get("bot") or {}
    if layer in ("publish_channels", "archive_index", "bot_delivery"):
        return bool(plat.get(layer, True) and bot_cfg.get(layer, True))
    return bool(plat.get(layer, True))


def get_bot_for_source(
    src_forum_id: int,
    src_topic_id: int,
    branch: str = "ads",
    plat: dict | None = None,
) -> dict | None:
    for b in list_bots_config(plat):
        if not b.get("enabled", True):
            continue
        if (
            b.get("source_forum_id") == src_forum_id
            and b.get("source_topic_id") == src_topic_id
            and b.get("branch", "ads") == branch
        ):
            return b
    return None


def mask_bots_for_api(bots: list[dict]) -> list[dict]:
    out = []
    for b in bots:
        m = _normalize_bot(b)
        if m.get("token"):
            m["has_token"] = True
            m["token"] = ""
        else:
            m["has_token"] = bool(m.get("has_token"))
        out.append(m)
    return out
