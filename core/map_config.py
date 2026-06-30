"""Xếp bài & map config — per-topic, sync topic_map.txt."""

from __future__ import annotations

import os
from typing import Any

from core.config_store import load_auto_config, topic_key, upsert_topic_source

TOPIC_MAP_TXT = "topic_map.txt"


def resolve_xep_settings(
    topic_cfg: dict[str, Any],
    global_cfg: dict[str, Any],
    picked_cmd: str = "",
) -> dict[str, Any]:
    """Trả cpa, mode, auto_xep (True = tự xếp không menu)."""
    g_mode = global_cfg.get("xepbai_mode") or _read_global_xepbai_mode()
    g_wl = set(x.lower() for x in (global_cfg.get("xepbai_whitelist_cmds") or _read_global_whitelist()))

    t_mode = topic_cfg.get("xepbai_mode")  # inherit | on | off
    if t_mode is None or t_mode == "inherit":
        xepbai_on = g_mode == "on"
    else:
        xepbai_on = str(t_mode).lower() == "on"

    wl = g_wl | set(x.lower() for x in topic_cfg.get("xepbai_whitelist_cmds") or [])
    cmd = (picked_cmd or "").lower().lstrip("/")
    force_manual = cmd and cmd in wl

    cpa = topic_cfg.get("default_cpa") or global_cfg.get("xepbai_off_default_cpa") or 1
    mode = topic_cfg.get("default_mode") or global_cfg.get("xepbai_off_default_mode") or "normal"
    use_ads = topic_cfg.get("use_ads")
    if use_ads is None:
        use_ads = True

    return {
        "default_cpa": int(cpa),
        "mode": str(mode),
        "use_ads": bool(use_ads),
        "auto_xep": not xepbai_on or not force_manual,
        "force_manual": force_manual,
        "xepbai_on": xepbai_on,
    }


def _read_global_xepbai_mode() -> str:
    if not os.path.exists(TOPIC_MAP_TXT):
        return "on"
    for line in open(TOPIC_MAP_TXT, encoding="utf-8"):
        s = line.strip().split("#", 1)[0].strip()
        if s.lower().startswith("@xepbai"):
            _, _, v = s.partition("=")
            return "off" if v.strip().lower() == "off" else "on"
    return "on"


def _read_global_whitelist() -> list[str]:
    if not os.path.exists(TOPIC_MAP_TXT):
        return []
    for line in open(TOPIC_MAP_TXT, encoding="utf-8"):
        s = line.strip().split("#", 1)[0].strip()
        if s.lower().startswith("@xepbaiwhite"):
            _, _, v = s.partition("=")
            return [x.strip() for x in v.replace(" ", ",").split(",") if x.strip()]
    return []


def sync_topic_to_map_file(entry: dict) -> None:
    """Ghi 1 dòng map vào topic_map.txt (giữ @xepbai directives)."""
    title = entry.get("topic_title") or ""
    cmds = entry.get("mapped_cmds") or []
    if not title or not cmds:
        return
    cmd = cmds[0]
    src = entry.get("src_chat_id")
    tid = entry.get("topic_id")
    if src and tid:
        key = f"{src}:{tid}"
    elif src:
        key = f"{src}:{title}"
    else:
        key = title

    lines: list[str] = []
    if os.path.exists(TOPIC_MAP_TXT):
        with open(TOPIC_MAP_TXT, encoding="utf-8") as f:
            lines = f.read().splitlines()

    kept = []
    for ln in lines:
        cs = ln.strip().split("#", 1)[0].strip()
        if not cs or cs.startswith("@") or "=" not in cs:
            kept.append(ln)
            continue
        left = cs.partition("=")[0].strip().lower()
        if left == key.lower() or left == title.lower():
            continue
        kept.append(ln)

    kept.append(f"{key} = {cmd}  # {title}")
    with open(TOPIC_MAP_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")


def apply_start_link_to_topic(src_chat_id: int, topic_id: int, link: str) -> dict:
    from core.link_parser import parse_telegram_link

    parsed = parse_telegram_link(link)
    if not parsed or not parsed.get("msg_id"):
        raise ValueError("Link không hợp lệ — dùng dạng https://t.me/c/1234567890/5/678")

    if parsed.get("src_chat_id") and parsed["src_chat_id"] != src_chat_id:
        raise ValueError(
            f"Link thuộc chat {parsed['src_chat_id']}, khác cấu hình {src_chat_id}"
        )
    if parsed.get("topic_id") and topic_id and parsed["topic_id"] != topic_id:
        raise ValueError(
            f"Link thuộc topic {parsed['topic_id']}, khác cấu hình {topic_id}"
        )

    data = {
        "start_link": link.strip(),
        "start_msg_id": parsed["msg_id"],
        "cursor_msg_id": parsed["msg_id"],
        "pin_mode": "link",
    }
    return upsert_topic_source(src_chat_id, topic_id, data)


def clear_start_link(src_chat_id: int, topic_id: int) -> dict:
    return upsert_topic_source(src_chat_id, topic_id, {
        "start_link": "",
        "start_msg_id": None,
        "pin_mode": "latest",
    })
