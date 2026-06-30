"""Xếp bài & map config — per-topic, sync topic_map.txt."""

from __future__ import annotations

import os
from typing import Any

from core.config_store import load_auto_config, save_auto_config, topic_key, upsert_topic_source
from core.map_limits import (
    apply_cmd_limit,
    format_map_comment,
    format_map_rhs,
    normalize_exclusive_limits,
    normalize_media_limits,
    normalize_post_limits,
)

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


def _map_left_key(entry: dict) -> str:
    title = (entry.get("topic_title") or "").strip()
    src = int(entry.get("src_chat_id") or 0)
    tid = int(entry.get("topic_id") or 0)
    if src and tid:
        return f"{src}:{tid}"
    if src and title:
        return f"{src}:{title}"
    return title or f"{src}:{tid}"


def rebuild_topic_map_file() -> int:
    """Ghi lại toàn bộ topic_map.txt từ config (giữ dòng @xepbai)."""
    cfg = load_auto_config()
    topics = cfg.get("topic_sources") or {}
    kept: list[str] = []
    if os.path.exists(TOPIC_MAP_TXT):
        for ln in open(TOPIC_MAP_TXT, encoding="utf-8"):
            cs = ln.strip().split("#", 1)[0].strip()
            if not cs:
                kept.append(ln.rstrip())
            elif cs.startswith("@") or "=" not in cs:
                kept.append(ln.rstrip())
    else:
        kept = [
            "# ===== MAP TOPIC -> KÊNH (sửa trên web hoặc file này) =====",
            "# vitamin = pro@5",
            "",
        ]
    if kept and kept[-1] != "":
        kept.append("")

    n = 0
    seen: set[str] = set()
    for _k, entry in sorted(topics.items(), key=lambda kv: (kv[1].get("topic_title") or kv[0]).lower()):
        title = (entry.get("topic_title") or "").strip()
        cmds = entry.get("mapped_cmds") or []
        if not cmds:
            continue
        limits = normalize_post_limits(entry.get("map_post_limits"))
        mlimits = normalize_media_limits(entry.get("map_media_limits"))
        left = _map_left_key(entry)
        if not left:
            continue
        for cmd in cmds:
            c = str(cmd).strip().lstrip("/")
            if not c:
                continue
            posts = limits.get(c.lower())
            media = mlimits.get(c.lower())
            rhs = format_map_rhs(c, posts)
            comment = format_map_comment(posts, title, media=media)
            line = f"{left} = {rhs}  # {comment}"
            sig = line.lower()
            if sig in seen:
                continue
            seen.add(sig)
            kept.append(line)
            n += 1

    with open(TOPIC_MAP_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    return n


def sync_topic_to_map_file(entry: dict) -> None:
    """Đồng bộ topic_map.txt sau thay đổi config."""
    rebuild_topic_map_file()


def upsert_topic_mapping(
    *,
    topic_title: str,
    channel_cmd: str,
    post_count: int | None = None,
    media_count: int | None = None,
    src_chat_id: int | None = None,
    topic_id: int | None = None,
) -> dict:
    title = (topic_title or "").strip()
    cmd = (channel_cmd or "").strip().lstrip("/")
    if not title:
        raise ValueError("Cần tên topic")
    if not cmd:
        raise ValueError("Cần tên kênh (alias)")

    key, existing = find_topic_entry(
        src_chat_id=src_chat_id or 0,
        topic_id=topic_id or 0,
        topic_title=title,
    )
    if not key:
        sid, tid = int(src_chat_id or 0), int(topic_id or 0)
        key = topic_key(sid, tid) if sid and tid else f"title:{title.lower()}"

    entry = dict(existing or {})
    entry["topic_title"] = title
    entry["src_chat_id"] = int(src_chat_id or entry.get("src_chat_id") or 0)
    entry["topic_id"] = int(topic_id if topic_id is not None else entry.get("topic_id") or 0)

    cmds = list(entry.get("mapped_cmds") or [])
    if cmd not in cmds:
        cmds.append(cmd)
    entry["mapped_cmds"] = cmds

    if post_count is not None and int(post_count) > 0:
        apply_cmd_limit(entry, cmd, post_count=int(post_count))
    elif media_count is not None and int(media_count) > 0:
        apply_cmd_limit(entry, cmd, media_count=int(media_count))
    entry.setdefault("enabled", True)

    cfg = load_auto_config()
    cfg.setdefault("topic_sources", {})[key] = entry
    save_auto_config(cfg)
    rebuild_topic_map_file()
    return entry


def update_topic_mapping_cmd(
    *,
    src_chat_id: int = 0,
    topic_id: int = 0,
    topic_title: str = "",
    old_cmd: str,
    new_cmd: str,
    post_count: int | None = None,
    media_count: int | None = None,
) -> dict:
    o = (old_cmd or "").strip().lstrip("/").lower()
    n = (new_cmd or "").strip().lstrip("/")
    if not n:
        raise ValueError("Tên kênh không được trống")

    key, entry = find_topic_entry(
        src_chat_id=src_chat_id,
        topic_id=topic_id,
        topic_title=topic_title,
    )
    if not entry:
        raise ValueError("Không tìm thấy topic")

    entry = dict(entry)
    cmds = [n if str(c).lower().lstrip("/") == o else c for c in (entry.get("mapped_cmds") or [])]
    if n not in cmds:
        cmds.append(n)
    entry["mapped_cmds"] = cmds

    limits = normalize_post_limits(entry.get("map_post_limits"))
    if o in limits:
        prev = limits.pop(o)
        if post_count is not None and int(post_count) > 0:
            limits[n.lower()] = int(post_count)
        elif n.lower() not in limits:
            limits[n.lower()] = prev
    elif post_count is not None and int(post_count) > 0:
        limits[n.lower()] = int(post_count)
    entry["map_post_limits"] = limits

    mlimits = normalize_media_limits(entry.get("map_media_limits"))
    if o in mlimits:
        prev_m = mlimits.pop(o)
        if media_count is not None and int(media_count) > 0:
            mlimits[n.lower()] = int(media_count)
        elif n.lower() not in mlimits:
            mlimits[n.lower()] = prev_m
    elif media_count is not None and int(media_count) > 0:
        mlimits[n.lower()] = int(media_count)
    entry["map_media_limits"] = mlimits

    cfg = load_auto_config()
    cfg.setdefault("topic_sources", {})[key] = entry
    save_auto_config(cfg)
    rebuild_topic_map_file()
    return entry


def delete_topic_entry(
    *,
    src_chat_id: int = 0,
    topic_id: int = 0,
    topic_title: str = "",
) -> bool:
    key, _ = find_topic_entry(
        src_chat_id=src_chat_id,
        topic_id=topic_id,
        topic_title=topic_title,
    )
    if not key:
        return False
    cfg = load_auto_config()
    cfg.get("topic_sources", {}).pop(key, None)
    save_auto_config(cfg)
    rebuild_topic_map_file()
    return True


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


def find_topic_entry(
    *,
    src_chat_id: int | None = None,
    topic_id: int | None = None,
    topic_title: str | None = None,
) -> tuple[str | None, dict | None]:
    """Tìm topic theo id hoặc tên (kể cả key title:... sau import)."""
    cfg = load_auto_config()
    topics = cfg.get("topic_sources") or {}

    if src_chat_id is not None and topic_id is not None:
        key = topic_key(src_chat_id, topic_id)
        if key in topics:
            return key, topics[key]

    title = (topic_title or "").strip().lower()
    if title:
        title_key = f"title:{title}"
        if title_key in topics:
            return title_key, topics[title_key]
        for k, t in topics.items():
            if (t.get("topic_title") or "").strip().lower() == title:
                return k, t

    return None, None


def apply_start_link_flexible(
    link: str,
    *,
    src_chat_id: int | None = None,
    topic_id: int | None = None,
    topic_title: str | None = None,
) -> dict:
    """Set link bắt đầu — tự lấy chat/topic từ link nếu map chỉ có tên."""
    from core.link_parser import parse_telegram_link
    from core.config_store import load_auto_config, save_auto_config, topic_key

    parsed = parse_telegram_link(link)
    if not parsed or not parsed.get("msg_id"):
        raise ValueError("Link không hợp lệ — dùng dạng https://t.me/c/1234567890/5/678")

    old_key, entry = find_topic_entry(
        src_chat_id=src_chat_id,
        topic_id=topic_id or 0,
        topic_title=topic_title,
    )
    entry = dict(entry or {})
    if topic_title and not entry.get("topic_title"):
        entry["topic_title"] = topic_title.strip()

    new_src = parsed.get("src_chat_id") or entry.get("src_chat_id") or src_chat_id or 0
    new_tid = parsed.get("topic_id")
    if new_tid is None:
        new_tid = entry.get("topic_id") or topic_id or 0

    if src_chat_id and new_src and int(src_chat_id) not in (0, int(new_src)):
        raise ValueError(f"Link thuộc chat {new_src}, khác cấu hình {src_chat_id}")
    if topic_id and new_tid and int(topic_id) not in (0, int(new_tid)):
        raise ValueError(f"Link thuộc topic {new_tid}, khác cấu hình {topic_id}")

    entry.update({
        "start_link": link.strip(),
        "start_msg_id": parsed["msg_id"],
        "cursor_msg_id": parsed["msg_id"],
        "pin_mode": "link",
        "src_chat_id": int(new_src),
        "topic_id": int(new_tid),
    })

    cfg = load_auto_config()
    topics = cfg.setdefault("topic_sources", {})
    new_key = topic_key(int(new_src), int(new_tid))
    if old_key and old_key != new_key:
        topics.pop(old_key, None)
    topics[new_key] = entry
    save_auto_config(cfg)
    return entry
