"""Kênh / folder / topic map — nhánh ads vs up bài (plain)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

BRANCH_ADS = "ads"
BRANCH_PLAIN = "plain"

CHANNEL_FILES = {
    BRANCH_ADS: "channels.json",
    BRANCH_PLAIN: "plain_channels.json",
}
FOLDER_FILES = {
    BRANCH_ADS: "folders.json",
    BRANCH_PLAIN: "plain_folders.json",
}
TOPIC_MAP_FILES = {
    BRANCH_ADS: "topic_map.txt",
    BRANCH_PLAIN: "plain_topic_map.txt",
}

TOPIC_MAP_TEMPLATE = (
    "# ===== MAP TOPIC -> KÊNH (nhánh Up bài — sửa tay) =====\n"
    "# Mỗi dòng:   tên_topic = tên_kênh\n"
    "# Scoped:     chat_id:tên_topic = tên_kênh\n"
    "#\n"
    "# @xepbai = on\n"
    "# @xepbaiwhite =\n"
)

_channels_cache: dict[str, list[dict] | None] = {BRANCH_ADS: None, BRANCH_PLAIN: None}


def _branch(branch: str) -> str:
    b = (branch or BRANCH_ADS).strip().lower()
    if b not in CHANNEL_FILES:
        raise ValueError(f"branch không hợp lệ: {branch}")
    return b


def channels_file(branch: str = BRANCH_ADS) -> str:
    return CHANNEL_FILES[_branch(branch)]


def folders_file(branch: str = BRANCH_ADS) -> str:
    return FOLDER_FILES[_branch(branch)]


def topic_map_file(branch: str = BRANCH_ADS) -> str:
    return TOPIC_MAP_FILES[_branch(branch)]


def invalidate_channels_cache(branch: str | None = None) -> None:
    if branch:
        _channels_cache[_branch(branch)] = None
    else:
        for k in _channels_cache:
            _channels_cache[k] = None


def normalize_channel_list(data: Any) -> list[dict]:
    """Chuẩn hóa channels.json cũ — list, dict id→object, hoặc {channels: [...]}."""
    if not data:
        return []
    if isinstance(data, list):
        out: list[dict] = []
        for item in data:
            if isinstance(item, dict) and item.get("id") is not None:
                out.append({
                    "id": item["id"],
                    "title": item.get("title") or str(item["id"]),
                    "username": item.get("username") or "",
                    "alias": item.get("alias") or "",
                })
            elif isinstance(item, (str, int)):
                try:
                    cid = int(item)
                    out.append({"id": cid, "title": str(cid), "username": "", "alias": ""})
                except (TypeError, ValueError):
                    pass
        return out
    if isinstance(data, dict):
        if isinstance(data.get("channels"), list):
            return normalize_channel_list(data["channels"])
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                ch = dict(v)
                if ch.get("id") is None:
                    try:
                        ch["id"] = int(k)
                    except (TypeError, ValueError):
                        continue
                ch.setdefault("title", str(ch["id"]))
                ch.setdefault("username", "")
                ch.setdefault("alias", "")
                out.append(ch)
        return out
    return []


def load_channels(branch: str = BRANCH_ADS) -> list[dict]:
    b = _branch(branch)
    cached = _channels_cache.get(b)
    if cached is not None:
        return list(cached)
    path = channels_file(b)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        channels = normalize_channel_list(data)
        _channels_cache[b] = channels
        return list(channels)
    _channels_cache[b] = []
    return []


def save_channels(channels: list[dict], branch: str = BRANCH_ADS) -> None:
    b = _branch(branch)
    normalized = normalize_channel_list(channels)
    _channels_cache[b] = normalized
    with open(channels_file(b), "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)


def rewrite_channels_file(branch: str = BRANCH_ADS) -> int:
    """Đọc lại file kênh, chuẩn hóa format list — trả số kênh."""
    invalidate_channels_cache(branch)
    channels = load_channels(branch)
    if not channels:
        return 0
    save_channels(channels, branch)
    return len(channels)


def load_folders(branch: str = BRANCH_ADS) -> list[dict]:
    path = folders_file(_branch(branch))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_folders(folders: list[dict], branch: str = BRANCH_ADS) -> None:
    with open(folders_file(_branch(branch)), "w", encoding="utf-8") as f:
        json.dump(folders, f, ensure_ascii=False, indent=2)


def remember_folder(slug: str, title: str = "", branch: str = BRANCH_ADS) -> bool:
    folders = load_folders(branch)
    for fd in folders:
        if fd.get("slug") == slug:
            if title and not fd.get("title"):
                fd["title"] = title
                save_folders(folders, branch)
            return False
    folders.append({"slug": slug, "title": title or slug, "added_at": int(time.time())})
    save_folders(folders, branch)
    return True


def extract_folder_slug(link: str) -> str | None:
    m = re.search(r"addlist/([A-Za-z0-9_+=-]+)", (link or "").strip())
    return m.group(1) if m else None


def add_folder_link(link: str, branch: str = BRANCH_ADS) -> dict[str, Any]:
    slug = extract_folder_slug(link)
    if not slug:
        raise ValueError("Link folder không hợp lệ — dùng https://t.me/addlist/xxxxx")
    remember_folder(slug, slug, branch)
    return {"slug": slug, "title": slug}


def set_channel_alias(channel_id: int, alias: str, branch: str = BRANCH_ADS) -> dict:
    channels = load_channels(branch)
    cid = str(channel_id)
    found = None
    for ch in channels:
        if str(ch.get("id")) == cid:
            ch["alias"] = (alias or "").strip()
            found = ch
            break
    if not found:
        raise ValueError(f"Không tìm thấy kênh {channel_id}")
    save_channels(channels, branch)
    invalidate_channels_cache(branch)
    return found


def load_channels_by_ids(ids: list[int], branch: str = BRANCH_ADS) -> list[dict]:
    if not ids:
        return []
    all_ch = load_channels(branch)
    idset = {str(i) for i in ids}
    order = {int(i): idx for idx, i in enumerate(ids)}
    picked = [c for c in all_ch if str(c.get("id")) in idset]
    picked.sort(key=lambda c: order.get(int(c["id"]), 9999))
    return picked


def ensure_topic_map(branch: str = BRANCH_PLAIN) -> None:
    path = topic_map_file(branch)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(TOPIC_MAP_TEMPLATE)


def load_topic_map_text(branch: str = BRANCH_PLAIN) -> str:
    ensure_topic_map(branch)
    with open(topic_map_file(branch), "r", encoding="utf-8") as f:
        return f.read()


def save_topic_map_text(text: str, branch: str = BRANCH_PLAIN) -> str:
    ensure_topic_map(branch)
    with open(topic_map_file(branch), "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return text


def _strip_junk(s: str) -> str:
    return re.sub(r"[^\w]", "", (s or "").strip(), flags=re.UNICODE)


def get_cmd_key(title: str, alias: str = "") -> str:
    if alias and alias.strip():
        return _strip_junk(alias.strip()).lower()
    parts = (title or "").strip().split(None, 1)
    raw = parts[1] if len(parts) >= 2 else (parts[0] if parts else "")
    return _strip_junk(raw).lower()


def all_channel_cmds(branch: str = BRANCH_PLAIN) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for ch in load_channels(branch):
        k = get_cmd_key(ch.get("title") or "", ch.get("alias") or "")
        if k:
            groups.setdefault(k, []).append(ch)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def gen_topic_map(branch: str = BRANCH_PLAIN) -> tuple[int, int]:
    """Sinh plain_topic_map.txt từ kênh nhánh plain — giữ mapping cũ."""
    path = topic_map_file(branch)
    existing: list[tuple[str, str]] = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            s = line.strip().split("#", 1)[0].strip()
            if not s or s.startswith("@") or "=" not in s:
                continue
            left, _, right = s.partition("=")
            if left.strip() and right.strip():
                existing.append((left.strip(), right.strip().lstrip("/")))

    mapped_cmds = {c.lower() for _, c in existing}
    groups = all_channel_cmds(branch)
    lines = [TOPIC_MAP_TEMPLATE.rstrip("\n"), ""]
    if existing:
        lines.append("# ===== ĐÃ MAP =====")
        for topic, cmd in existing:
            lines.append(f"{topic} = {cmd}")
        lines.append("")
    lines.append("# ===== ĐIỀN TÊN TOPIC VÀO TRƯỚC DẤU = =====")
    n_new = 0
    for cmd, chs in groups:
        if cmd.lower() in mapped_cmds:
            continue
        titles = ", ".join((c.get("title") or "") for c in chs)
        lines.append(f" = {cmd}    # {titles}")
        n_new += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return len(existing), n_new
