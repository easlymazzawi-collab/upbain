"""Hàng đợi lệnh từ web → userbot xử lý."""

import time
import uuid
from typing import Any

from core.config_store import load_auto_config, save_auto_config


def queue_action(action_type: str, params: dict | None = None) -> dict:
    cfg = load_auto_config()
    action = {
        "id": uuid.uuid4().hex[:12],
        "type": action_type,
        "params": params or {},
        "ts": int(time.time()),
        "status": "pending",
    }
    cfg.setdefault("pending_actions", []).append(action)
    save_auto_config(cfg)
    return action


def pop_next_action() -> dict | None:
    cfg = load_auto_config()
    actions = cfg.get("pending_actions") or []
    if not actions:
        return None
    action = actions.pop(0)
    cfg["pending_actions"] = actions
    save_auto_config(cfg)
    return action


def list_pending_actions() -> list[dict]:
    return load_auto_config().get("pending_actions") or []


def action_labels() -> dict[str, str]:
    return {
        "run_full_cycle": "Chạy full (topic + /all)",
        "run_all_topics": "Chạy tất cả topic nguồn",
        "run_all_task": "Chạy /all task",
        "refresh_all_batch": "Quét batch /all",
        "run_topic": "Chạy 1 topic",
        "run_plain_topic": "Chạy 1 topic Up bài",
        "scan_topic": "Scan kho topic",
        "sync_folders": "Sync folder → cập nhật kênh",
        "sync_plain_folders": "Sync folder nhánh Up bài",
        "check_channels": "Check kênh chết",
        "check_plain_channels": "Check kênh chết (Up bài)",
        "gen_plain_topic_map": "Sinh plain_topic_map.txt",
        "xep_preview": "Xem trước xếp bài",
        "apply_start_link": "Set link bắt đầu",
        "clear_start_link": "Xóa link bắt đầu",
    }
