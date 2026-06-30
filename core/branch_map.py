"""Topic map CRUD — nhánh ads vs Up bài (plain), đồng bộ file + auto_config."""

from __future__ import annotations

import os
from typing import Any

from core.channel_store import BRANCH_ADS, BRANCH_PLAIN, topic_map_file
from core.config_store import load_auto_config, save_auto_config, topic_key
from core.map_limits import (
    apply_cmd_limit,
    format_map_comment,
    format_map_rhs,
    normalize_exclusive_limits,
    normalize_media_limits,
    normalize_post_limits,
)


def sources_key(branch: str) -> str:
    return "plain_topic_sources" if branch == BRANCH_PLAIN else "topic_sources"


def _branch(branch: str) -> str:
    b = (branch or BRANCH_ADS).strip().lower()
    if b not in (BRANCH_ADS, BRANCH_PLAIN):
        raise ValueError(f"branch không hợp lệ: {branch}")
    return b


def list_topic_entries(branch: str = BRANCH_ADS) -> list[dict]:
    cfg = load_auto_config()
    return list(cfg.get(sources_key(_branch(branch)), {}).values())


def _map_left_key(entry: dict) -> str:
    title = (entry.get("topic_title") or "").strip()
    src = int(entry.get("src_chat_id") or 0)
    tid = int(entry.get("topic_id") or 0)
    if src and tid:
        return f"{src}:{tid}"
    if src and title:
        return f"{src}:{title}"
    return title or f"{src}:{tid}"


def rebuild_map_file(branch: str = BRANCH_ADS) -> int:
    """Ghi topic_map.txt hoặc plain_topic_map.txt từ config."""
    branch = _branch(branch)
    path = topic_map_file(branch)
    cfg = load_auto_config()
    topics = cfg.get(sources_key(branch), {}) or {}

    kept: list[str] = []
    if os.path.exists(path):
        for ln in open(path, encoding="utf-8"):
            cs = ln.strip().split("#", 1)[0].strip()
            if not cs:
                kept.append(ln.rstrip())
            elif cs.startswith("@") or "=" not in cs:
                kept.append(ln.rstrip())
    else:
        header = (
            "# ===== MAP TOPIC -> KÊNH (nhánh Up bài) ====="
            if branch == BRANCH_PLAIN
            else "# ===== MAP TOPIC -> KÊNH (nhánh ads) ====="
        )
        kept = [header, "# vitamin = pro@5", ""]

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

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept) + "\n")
    return n


def find_topic_entry(
    *,
    branch: str = BRANCH_ADS,
    src_chat_id: int | None = None,
    topic_id: int | None = None,
    topic_title: str | None = None,
) -> tuple[str | None, dict | None]:
    branch = _branch(branch)
    cfg = load_auto_config()
    topics = cfg.get(sources_key(branch), {}) or {}

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


def upsert_topic_mapping(
    *,
    branch: str = BRANCH_ADS,
    topic_title: str,
    channel_cmd: str,
    post_count: int | None = None,
    media_count: int | None = None,
    src_chat_id: int | None = None,
    topic_id: int | None = None,
) -> dict:
    branch = _branch(branch)
    title = (topic_title or "").strip()
    cmd = (channel_cmd or "").strip().lstrip("/")
    if not title:
        raise ValueError("Cần tên topic")
    if not cmd:
        raise ValueError("Cần tên kênh (alias)")

    key, existing = find_topic_entry(
        branch=branch,
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
    entry.setdefault("enabled", True)
    entry["use_ads"] = False if branch == BRANCH_PLAIN else entry.get("use_ads", True)

    cmds = list(entry.get("mapped_cmds") or [])
    if cmd not in cmds:
        cmds.append(cmd)
    entry["mapped_cmds"] = cmds

    if post_count is not None and int(post_count) > 0:
        apply_cmd_limit(entry, cmd, post_count=int(post_count))
    elif media_count is not None and int(media_count) > 0:
        apply_cmd_limit(entry, cmd, media_count=int(media_count))

    cfg = load_auto_config()
    cfg.setdefault(sources_key(branch), {})[key] = entry
    save_auto_config(cfg)
    rebuild_map_file(branch)
    return entry


def update_topic_mapping_cmd(
    *,
    branch: str = BRANCH_ADS,
    src_chat_id: int = 0,
    topic_id: int = 0,
    topic_title: str = "",
    old_cmd: str,
    new_cmd: str,
    post_count: int | None = None,
    media_count: int | None = None,
) -> dict:
    branch = _branch(branch)
    o = (old_cmd or "").strip().lstrip("/").lower()
    n = (new_cmd or "").strip().lstrip("/")
    if not n:
        raise ValueError("Tên kênh không được trống")

    key, entry = find_topic_entry(
        branch=branch,
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
    cfg.setdefault(sources_key(branch), {})[key] = entry
    save_auto_config(cfg)
    rebuild_map_file(branch)
    return entry


def delete_topic_entry(
    *,
    branch: str = BRANCH_ADS,
    src_chat_id: int = 0,
    topic_id: int = 0,
    topic_title: str = "",
) -> bool:
    branch = _branch(branch)
    key, _ = find_topic_entry(
        branch=branch,
        src_chat_id=src_chat_id,
        topic_id=topic_id,
        topic_title=topic_title,
    )
    if not key:
        return False
    cfg = load_auto_config()
    cfg.get(sources_key(branch), {}).pop(key, None)
    save_auto_config(cfg)
    rebuild_map_file(branch)
    return True


def upsert_topic_entry(
    branch: str,
    src_chat_id: int,
    topic_id: int,
    data: dict,
) -> dict:
    branch = _branch(branch)
    cfg = load_auto_config()
    key = topic_key(src_chat_id, topic_id)
    sk = sources_key(branch)
    entry = cfg.setdefault(sk, {}).get(key, {})
    entry.update(data)
    entry["src_chat_id"] = src_chat_id
    entry["topic_id"] = topic_id
    if branch == BRANCH_PLAIN:
        entry["use_ads"] = False
    cfg[sk][key] = entry
    save_auto_config(cfg)
    return entry


def apply_start_link_flexible(
    link: str,
    *,
    branch: str = BRANCH_ADS,
    src_chat_id: int | None = None,
    topic_id: int | None = None,
    topic_title: str | None = None,
) -> dict:
    from core.link_parser import parse_telegram_link

    branch = _branch(branch)
    parsed = parse_telegram_link(link)
    if not parsed or not parsed.get("msg_id"):
        raise ValueError("Link không hợp lệ — dùng dạng https://t.me/c/1234567890/5/678")

    old_key, entry = find_topic_entry(
        branch=branch,
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
        "enabled": True,
        "use_ads": False if branch == BRANCH_PLAIN else entry.get("use_ads", True),
    })

    cfg = load_auto_config()
    sk = sources_key(branch)
    topics = cfg.setdefault(sk, {})
    new_key = topic_key(int(new_src), int(new_tid))
    if old_key and old_key != new_key:
        topics.pop(old_key, None)
    topics[new_key] = entry
    save_auto_config(cfg)
    rebuild_map_file(branch)
    return entry


def sync_map_file_to_config(branch: str = BRANCH_PLAIN) -> int:
    """Đọc file map → bổ sung plain_topic_sources / topic_sources."""
    from core.import_legacy import parse_topic_map_text, merge_topic_cmds

    branch = _branch(branch)
    path = topic_map_file(branch)
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as f:
        topics = parse_topic_map_text(f.read())
    if not topics:
        return 0

    n = 0
    cfg = load_auto_config()
    sk = sources_key(branch)
    for topic in topics:
        if branch == BRANCH_PLAIN:
            topic["use_ads"] = False
        sid = int(topic.get("src_chat_id") or 0)
        tid = int(topic.get("topic_id") or 0)
        if sid and tid:
            key = topic_key(sid, tid)
            cur = cfg.get(sk, {}).get(key, {})
            cfg.setdefault(sk, {})[key] = merge_topic_cmds(cur, topic) if cur else topic
            n += 1
        elif sid and topic.get("topic_title"):
            key = topic_key(sid, 0)
            cur = cfg.get(sk, {}).get(key, {})
            cfg.setdefault(sk, {})[key] = merge_topic_cmds(cur, topic)
            n += 1
        elif topic.get("topic_title"):
            preview_key = f"title:{topic['topic_title'].lower()}"
            cur = cfg.get(sk, {}).get(preview_key, {})
            cfg.setdefault(sk, {})[preview_key] = merge_topic_cmds(cur, topic) if cur else topic
            n += 1
    save_auto_config(cfg)
    return n
