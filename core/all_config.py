"""Cấu hình /all — link nguồn + kênh đích (all + up bài)."""

from __future__ import annotations

import json
import os
from typing import Any

from core.config_store import load_auto_config, save_auto_config
from core.link_parser import parse_telegram_link
from core.settings import CHANNELS_FILE


def apply_link_fields(task: dict[str, Any]) -> dict[str, Any]:
    """Parse link → chat/topic/msg; giữ default xếp bài."""
    out = dict(task)
    link = (out.get("start_link") or out.get("source_link") or "").strip()
    if link:
        out["start_link"] = link
        parsed = parse_telegram_link(link)
        if parsed:
            if parsed.get("src_chat_id"):
                out["source_chat_id"] = parsed["src_chat_id"]
            if parsed.get("topic_id") is not None:
                out["source_topic_id"] = parsed["topic_id"]
            if parsed.get("msg_id"):
                out["start_msg_id"] = parsed["msg_id"]
                out["cursor_msg_id"] = parsed["msg_id"]
                out["pin_mode"] = "link"
    out.setdefault("use_ads", False)
    out.setdefault("xep_cpa", 1)
    out.setdefault("xep_mode", "normal")
    out.setdefault("run_after_regular", True)
    out.setdefault("pin_mode", out.get("pin_mode") or "latest")
    return out


def merge_all_task(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = load_auto_config()
    cur = dict(cfg.get("all_task") or {})
    for k, v in patch.items():
        if v is not None:
            cur[k] = v
    if patch.get("source_link") is not None:
        cur["start_link"] = patch["source_link"]
    cur = apply_link_fields(cur)
    cfg["all_task"] = cur
    save_auto_config(cfg)
    return cur


def merge_plain_task(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = load_auto_config()
    cur = dict(cfg.get("plain_task") or {"enabled": False, "selected_channel_ids": [], "skip_last_posts": 0})
    for k, v in patch.items():
        if v is not None:
            cur[k] = v
    cur["skip_last_posts"] = max(0, int(cur.get("skip_last_posts") or 0))
    cfg["plain_task"] = cur
    save_auto_config(cfg)
    return cur


def plain_skip_last(cfg: dict | None = None) -> int:
    cfg = cfg or load_auto_config()
    return max(0, int((cfg.get("plain_task") or {}).get("skip_last_posts") or 0))


def trim_posts_for_plain(posts: list, skip_last: int | None = None, cfg: dict | None = None) -> list:
    """Bỏ N bài cuối khỏi lượt up nhánh không ads (AtomicPost hoặc msg_id list)."""
    skip = plain_skip_last(cfg) if skip_last is None else max(0, int(skip_last or 0))
    if skip <= 0 or not posts:
        return list(posts)
    if skip >= len(posts):
        return []
    return list(posts[:-skip])


def destination_channel_ids(cfg: dict | None = None) -> list[int]:
    """Kênh nhận /all + nhánh up bài (dedupe)."""
    cfg = cfg or load_auto_config()
    ids: list[int] = []
    seen: set[int] = set()
    all_t = cfg.get("all_task") or {}
    plain = cfg.get("plain_task") or {}
    if all_t.get("enabled"):
        for cid in all_t.get("selected_channel_ids") or []:
            n = int(cid)
            if n not in seen:
                seen.add(n)
                ids.append(n)
    if plain.get("enabled"):
        for cid in plain.get("selected_channel_ids") or []:
            n = int(cid)
            if n not in seen:
                seen.add(n)
                ids.append(n)
    return ids


def _load_channels_by_ids(ids: list[int]) -> list[dict]:
    if not ids:
        return []
    if not os.path.exists(CHANNELS_FILE):
        return []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        all_ch = json.load(f)
    idset = {str(i) for i in ids}
    order = {int(i): idx for idx, i in enumerate(ids)}
    picked = [c for c in all_ch if str(c.get("id")) in idset]
    picked.sort(key=lambda c: order.get(int(c["id"]), 9999))
    return picked


def load_destination_channels(cfg: dict | None = None) -> list[dict]:
    return _load_channels_by_ids(destination_channel_ids(cfg))


def split_destination_channels(cfg: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Kênh tab /all (có thể xen ads) vs tab Up bài (không ads)."""
    cfg = cfg or load_auto_config()
    all_t = cfg.get("all_task") or {}
    plain = cfg.get("plain_task") or {}
    all_ids = [int(i) for i in all_t.get("selected_channel_ids") or []] if all_t.get("enabled") else []
    plain_ids = [int(i) for i in plain.get("selected_channel_ids") or []] if plain.get("enabled") else []
    return _load_channels_by_ids(all_ids), _load_channels_by_ids(plain_ids)


def all_source_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or load_auto_config()
    t = cfg.get("all_task") or {}
    return bool(t.get("enabled") and t.get("source_chat_id") and t.get("source_topic_id"))
