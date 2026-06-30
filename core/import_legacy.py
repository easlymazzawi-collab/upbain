"""Import .env và file data cũ → auto_config.json + file workspace."""

from __future__ import annotations

import io
import json
import os
import zipfile
from copy import deepcopy
from typing import Any

from core.config_store import (
    AUTO_CONFIG_FILE,
    DATA_DIR,
    DEFAULT_AUTO_CONFIG,
    INVENTORY_FILE,
    load_auto_config,
    save_auto_config,
    save_inventory,
    topic_key,
    upsert_topic_source,
)
from core.settings import CHANNELS_FILE
from core.map_limits import parse_map_line, normalize_post_limits, normalize_media_limits, normalize_exclusive_limits
from core.channel_store import BRANCH_PLAIN, invalidate_channels_cache, rewrite_channels_file

ROOT_FILES = {
    "channels.json": CHANNELS_FILE,
    "folders.json": "folders.json",
    "topic_map.txt": "topic_map.txt",
    "topic_rr.json": "topic_rr.json",
    "failed_msgs.json": "failed_msgs.json",
    "plain_channels.json": "plain_channels.json",
    "plain_folders.json": "plain_folders.json",
    "plain_topic_map.txt": "plain_topic_map.txt",
}

DATA_FILES = {
    "inventory.json": INVENTORY_FILE,
    "auto_config.json": AUTO_CONFIG_FILE,
}

SESSION_SUFFIXES = (".session", ".session-journal")

ENV_TO_GLOBAL: dict[str, tuple[str, type]] = {
    "API_ID": ("api_id", int),
    "API_HASH": ("api_hash", str),
    "INTERMEDIATE_CHAT": ("intermediate_chat", int),
    "ADS_CHAT": ("ads_chat", int),
    "WEB_PORT": ("web_port", int),
    "WEB_TOKEN": ("web_token", str),
    "BOT_TOKEN": ("bot_token", str),
    "TELEGRAM_BOT_TOKEN": ("bot_token", str),
    "PIN_BOT_TOKEN": ("pin_bot_token", str),
    "NOTIFY_CHAT_ID": ("notify_chat_id", int),
    "NOTIFICATION_CHAT_ID": ("notify_chat_id", int),
}


def _cast_value(raw: str, typ: type) -> Any:
    raw = raw.strip().strip('"').strip("'")
    if typ is int:
        return int(raw)
    return raw


def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def env_to_global(env: dict[str, str]) -> dict[str, Any]:
    g: dict[str, Any] = {}
    for env_key, (cfg_key, typ) in ENV_TO_GLOBAL.items():
        if env_key in env and env[env_key]:
            try:
                g[cfg_key] = _cast_value(env[env_key], typ)
            except (ValueError, TypeError):
                pass
    return g


def _normalize_chat_id(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        n = int(raw)
        if n > 0 and not str(n).startswith("100"):
            return int(f"-100{n}")
        return n
    except ValueError:
        return None


def parse_topic_map_text(text: str) -> list[dict[str, Any]]:
    """topic_map.txt → danh sách topic_sources (partial ok)."""
    topics: list[dict[str, Any]] = []
    seen: set[str] = set()

    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        body, _, comment = s.partition("#")
        if "@" in body.split("=", 1)[0]:
            continue
        sep = "=" if "=" in body else (":" if ":" in body else None)
        if not sep:
            continue
        left, _, right = body.partition(sep)
        left = left.strip()
        right = right.strip()
        if not left or not right:
            continue

        cmd, media_n, post_n = parse_map_line(left, right, comment)
        if not cmd:
            continue

        src_chat_id: int | None = None
        topic_id = 0
        topic_title = ""

        if ":" in left:
            chat_part, _, topic_part = left.partition(":")
            src_chat_id = _normalize_chat_id(chat_part)
            topic_part = topic_part.strip()
            if topic_part.isdigit():
                topic_id = int(topic_part)
            else:
                topic_title = topic_part
        else:
            topic_title = left

        if src_chat_id is None and not topic_title:
            continue

        key = f"{src_chat_id}:{topic_id}:{topic_title.lower()}"
        if key in seen:
            continue
        seen.add(key)

        post_limits: dict[str, int] = {}
        media_limits: dict[str, int] = {}
        if post_n and post_n > 0:
            post_limits[cmd.lower()] = int(post_n)
        if media_n and media_n > 0:
            media_limits[cmd.lower()] = int(media_n)

        entry: dict[str, Any] = {
            "src_chat_id": src_chat_id or 0,
            "topic_id": topic_id,
            "topic_title": topic_title,
            "mapped_cmds": [cmd],
            "map_post_limits": post_limits,
            "map_media_limits": media_limits,
            "enabled": True,
            "_imported_from": "topic_map.txt",
        }
        topics.append(entry)
    return topics


def merge_topic_cmds(existing: dict, incoming: dict) -> dict:
    cmds = list(existing.get("mapped_cmds") or [])
    for c in incoming.get("mapped_cmds") or []:
        if c and c not in cmds:
            cmds.append(c)
    existing["mapped_cmds"] = cmds
    limits = normalize_post_limits(existing.get("map_post_limits"))
    limits.update(normalize_post_limits(incoming.get("map_post_limits")))
    mlimits = normalize_media_limits(existing.get("map_media_limits"))
    mlimits.update(normalize_media_limits(incoming.get("map_media_limits")))
    posts, media = normalize_exclusive_limits(limits, mlimits)
    if posts:
        existing["map_post_limits"] = posts
    if media:
        existing["map_media_limits"] = media
    elif "map_media_limits" in existing and not media:
        existing["map_media_limits"] = {}
    for k, v in incoming.items():
        if k in ("mapped_cmds", "map_post_limits", "map_media_limits"):
            continue
        if v is None or v == "" or v == 0:
            continue
        if k not in existing or not existing.get(k):
            existing[k] = v
    return existing


def merge_auto_config(existing: dict, incoming: dict) -> dict:
    out = deepcopy(existing)
    for section in ("global", "all_task", "plain_task", "schedule"):
        if section in incoming and isinstance(incoming[section], dict):
            if section == "global":
                for k, v in incoming["global"].items():
                    if v is None or v == "":
                        continue
                    out.setdefault("global", {})[k] = v
            elif section == "all_task":
                out["all_task"] = {**out.get("all_task", {}), **incoming["all_task"]}
            elif section == "plain_task":
                out["plain_task"] = {**out.get("plain_task", {}), **incoming["plain_task"]}
            elif section == "schedule":
                out.setdefault("global", {}).setdefault("schedule", {})
                out["global"]["schedule"].update(incoming["schedule"])

    for key, val in (incoming.get("topic_sources") or {}).items():
        cur = out.setdefault("topic_sources", {}).get(key, {})
        out["topic_sources"][key] = merge_topic_cmds(cur, val) if cur else val
    return out


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class ImportPreview:
    def __init__(self) -> None:
        self.env_keys: list[str] = []
        self.global_fields: list[str] = []
        self.root_files: list[str] = []
        self.data_files: list[str] = []
        self.sessions: list[str] = []
        self.topics_from_map: int = 0
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "env_keys": self.env_keys,
            "global_fields": self.global_fields,
            "root_files": self.root_files,
            "data_files": self.data_files,
            "sessions": self.sessions,
            "topics_from_map": self.topics_from_map,
            "warnings": self.warnings,
            "errors": self.errors,
        }


def _basename(name: str) -> str:
    return os.path.basename(name.replace("\\", "/"))


def workspace_root() -> str:
    """Thư mục làm việc của tool (nơi user giải nén file cũ)."""
    return os.getcwd()


def collect_workspace_files(root: str | None = None) -> list[tuple[str, bytes]]:
    """Đọc file data cũ đã có trên disk trong folder tool."""
    root = root or workspace_root()
    out: list[tuple[str, bytes]] = []

    for env_name in (".env", "env"):
        path = os.path.join(root, env_name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                out.append((env_name, f.read()))

    for base in ROOT_FILES:
        path = os.path.join(root, base)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                out.append((base, f.read()))

    for base, dest in DATA_FILES.items():
        path = os.path.join(root, dest)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                out.append((base, f.read()))

    try:
        for name in sorted(os.listdir(root)):
            low = name.lower()
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            if any(low.endswith(sfx) for sfx in SESSION_SUFFIXES):
                with open(path, "rb") as f:
                    out.append((name, f.read()))
            elif low.endswith(".zip"):
                with open(path, "rb") as f:
                    out.append((name, f.read()))
    except OSError:
        pass

    return out


def scan_workspace(root: str | None = None) -> tuple[ImportPreview, dict[str, Any], list[str]]:
    """Quét folder tool — preview + payload (chưa ghi)."""
    root = root or workspace_root()
    files = collect_workspace_files(root)
    preview = ImportPreview()
    if not files:
        preview.warnings.append(
            f"Không thấy file import trong {root} — cần .env, channels.json, topic_map.txt, "
            f"data/*.json hoặc file .zip backup (ví dụ 26-06-30_0642.zip)..."
        )
        return preview, {}, [root]

    preview, payload = analyze_uploads(files)
    found = [name for name, _ in files]
    return preview, payload, found


def import_from_workspace(root: str | None = None) -> dict[str, Any]:
    """Import trực tiếp từ file đã có trong folder tool."""
    preview, payload, found = scan_workspace(root)
    if preview.errors:
        raise ValueError("; ".join(preview.errors))
    if not found:
        raise ValueError(
            "Không tìm thấy file cũ trong folder tool. "
            "Đặt .env, channels.json, topic_map.txt... cùng thư mục với tool rồi thử lại."
        )
    applied = apply_import(payload)
    return {
        "ok": True,
        "preview": preview.to_dict(),
        "applied": applied,
        "found_files": found,
        "workspace": root or workspace_root(),
    }


def analyze_uploads(files: list[tuple[str, bytes]]) -> tuple[ImportPreview, dict[str, Any]]:
    """Phân tích file upload, trả preview + payload nội bộ (chưa ghi disk)."""
    preview = ImportPreview()
    payload: dict[str, Any] = {
        "env_global": {},
        "auto_config_merge": {},
        "inventory": None,
        "root": {},
        "data": {},
        "sessions": {},
        "topic_map_topics": [],
    }

    expanded: list[tuple[str, bytes]] = []
    for name, data in files:
        base = _basename(name).lower()
        if base.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        expanded.append((_basename(info.filename), zf.read(info)))
            except zipfile.BadZipFile:
                preview.errors.append(f"ZIP lỗi: {name}")
        else:
            expanded.append((_basename(name), data))

    for base, data in expanded:
        low = base.lower()

        if low == ".env" or low.endswith(".env") or low == "env":
            try:
                env = parse_env_text(data.decode("utf-8", errors="replace"))
                g = env_to_global(env)
                payload["env_global"].update(g)
                preview.env_keys = sorted(set(preview.env_keys + list(env.keys())))
                preview.global_fields = sorted(set(preview.global_fields + list(g.keys())))
            except Exception as e:
                preview.errors.append(f"Parse .env ({base}): {e}")
            continue

        if low in ROOT_FILES:
            payload["root"][ROOT_FILES[low]] = data
            preview.root_files.append(base)
            if low == "topic_map.txt":
                topics = parse_topic_map_text(data.decode("utf-8", errors="replace"))
                payload["topic_map_topics"] = topics
                preview.topics_from_map = len(topics)
            continue

        if low in DATA_FILES:
            dest = DATA_FILES[low]
            payload["data"][dest] = data
            preview.data_files.append(base)
            if low == "auto_config.json":
                try:
                    payload["auto_config_merge"] = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError as e:
                    preview.errors.append(f"auto_config.json lỗi: {e}")
            if low == "inventory.json":
                try:
                    payload["inventory"] = json.loads(data.decode("utf-8"))
                except json.JSONDecodeError as e:
                    preview.errors.append(f"inventory.json lỗi: {e}")
            continue

        if any(low.endswith(sfx) for sfx in SESSION_SUFFIXES):
            payload["sessions"][base] = data
            preview.sessions.append(base)
            continue

        preview.warnings.append(f"Bỏ qua file không nhận dạng: {base}")

    return preview, payload


def apply_import(payload: dict[str, Any]) -> dict[str, Any]:
    """Ghi file + merge config. Trả summary."""
    applied: dict[str, Any] = {
        "global_updated": [],
        "files_written": [],
        "topics_upserted": 0,
        "sessions": [],
    }

    cfg = load_auto_config()

    if payload.get("env_global"):
        for k, v in payload["env_global"].items():
            cfg["global"][k] = v
            applied["global_updated"].append(k)

    if payload.get("auto_config_merge"):
        cfg = merge_auto_config(cfg, payload["auto_config_merge"])
        applied["global_updated"].extend(
            [k for k in payload["auto_config_merge"].get("global", {}) if payload["auto_config_merge"]["global"].get(k)]
        )

    save_auto_config(cfg)

    for path, data in payload.get("root", {}).items():
        _write_bytes(path, data)
        applied["files_written"].append(path)

    for path, data in payload.get("data", {}).items():
        if path == AUTO_CONFIG_FILE:
            continue
        _write_bytes(path, data)
        applied["files_written"].append(path)

    if payload.get("inventory"):
        save_inventory(payload["inventory"])
        applied["files_written"].append(INVENTORY_FILE)

    for topic in payload.get("topic_map_topics") or []:
        sid = topic.get("src_chat_id") or 0
        tid = topic.get("topic_id") or 0
        if sid and tid:
            upsert_topic_source(sid, tid, topic)
            applied["topics_upserted"] += 1
        elif sid and topic.get("topic_title"):
            key = topic_key(sid, 0)
            cfg = load_auto_config()
            cur = cfg.get("topic_sources", {}).get(key, {})
            merged = merge_topic_cmds(cur, topic)
            cfg.setdefault("topic_sources", {})[key] = merged
            save_auto_config(cfg)
            applied["topics_upserted"] += 1
        elif topic.get("topic_title"):
            preview_key = f"title:{topic['topic_title'].lower()}"
            cfg = load_auto_config()
            cfg.setdefault("topic_sources", {})[preview_key] = topic
            save_auto_config(cfg)
            applied["topics_upserted"] += 1

    for name, data in payload.get("sessions", {}).items():
        _write_bytes(name, data)
        applied["sessions"].append(name)
        applied["files_written"].append(name)

    applied["global_updated"] = sorted(set(applied["global_updated"]))
    applied["files_written"] = sorted(set(applied["files_written"]))
    applied["topics_upserted"] += sync_topic_sources_from_map_file()
    from core.branch_map import sync_map_file_to_config
    from core.channel_store import BRANCH_PLAIN
    applied["plain_topics_upserted"] = sync_map_file_to_config(BRANCH_PLAIN)
    applied["channels_normalized"] = rewrite_channels_file()
    applied["plain_channels_normalized"] = rewrite_channels_file(BRANCH_PLAIN)
    invalidate_channels_cache()
    return applied


def sync_topic_sources_from_map_file(path: str = "topic_map.txt") -> int:
    """Đọc topic_map.txt trên disk → bổ sung topic_sources web (idempotent)."""
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0
    topics = parse_topic_map_text(text)
    if not topics:
        return 0
    n = 0
    for topic in topics:
        sid = int(topic.get("src_chat_id") or 0)
        tid = int(topic.get("topic_id") or 0)
        if sid and tid:
            upsert_topic_source(sid, tid, topic)
            n += 1
        elif sid and topic.get("topic_title"):
            cfg = load_auto_config()
            key = topic_key(sid, 0)
            cur = cfg.get("topic_sources", {}).get(key, {})
            cfg.setdefault("topic_sources", {})[key] = merge_topic_cmds(cur, topic)
            save_auto_config(cfg)
            n += 1
        elif topic.get("topic_title"):
            cfg = load_auto_config()
            preview_key = f"title:{topic['topic_title'].lower()}"
            cur = cfg.get("topic_sources", {}).get(preview_key, {})
            cfg.setdefault("topic_sources", {})[preview_key] = merge_topic_cmds(cur, topic) if cur else topic
            save_auto_config(cfg)
            n += 1
    return n
