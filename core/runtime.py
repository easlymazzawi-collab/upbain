"""Runtime state + activity log — web đọc real-time qua SSE."""

import os
import threading
import time
from copy import deepcopy
from typing import Any

from core.config_store import DATA_DIR

RUNTIME_FILE = os.path.join(DATA_DIR, "runtime.json")
_lock = threading.Lock()
_MAX_LOG = 200

DEFAULT_RUNTIME: dict[str, Any] = {
    "status": "idle",
    "current_task": "",
    "last_run_at": 0,
    "next_run_at": 0,
    "last_run_result": "",
    "updated_at": 0,
    "log": [],
    "waiting_topics": {},
    "pending_up": {},
    "all_batch_preview": None,
}


def _read() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(RUNTIME_FILE):
        return deepcopy(DEFAULT_RUNTIME)
    try:
        import json

        with open(RUNTIME_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("log", [])
            return data
    except Exception:
        pass
    return deepcopy(DEFAULT_RUNTIME)


def _write(data: dict) -> None:
    import json

    os.makedirs(DATA_DIR, exist_ok=True)
    data["updated_at"] = int(time.time())
    with open(RUNTIME_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_runtime() -> dict:
    with _lock:
        return _read()


def set_status(
    status: str,
    current_task: str = "",
    *,
    last_run_result: str | None = None,
    next_run_at: int | None = None,
) -> dict:
    with _lock:
        rt = _read()
        rt["status"] = status
        if current_task is not None:
            rt["current_task"] = current_task
        if last_run_result is not None:
            rt["last_run_result"] = last_run_result
        if next_run_at is not None:
            rt["next_run_at"] = next_run_at
        _write(rt)
        return rt


def append_log(level: str, message: str) -> None:
    with _lock:
        rt = _read()
        rt.setdefault("log", []).append({
            "ts": int(time.time()),
            "level": level,
            "message": message,
        })
        rt["log"] = rt["log"][-_MAX_LOG:]
        _write(rt)


def mark_run_start(task: str = "") -> None:
    with _lock:
        rt = _read()
        rt["status"] = "running"
        rt["current_task"] = task
        rt["last_run_at"] = int(time.time())
        _write(rt)


def mark_run_done(result: str = "ok") -> None:
    with _lock:
        rt = _read()
        rt["status"] = "idle"
        rt["current_task"] = ""
        rt["last_run_result"] = result
        _write(rt)


def pipeline_busy() -> bool:
    with _lock:
        return _read().get("status") == "running"


def set_topic_waiting(key: str, *, title: str, have_media: int, need_media: int) -> None:
    with _lock:
        rt = _read()
        rt.setdefault("waiting_topics", {})[key] = {
            "title": title,
            "have_media": have_media,
            "need_media": need_media,
            "updated_at": int(time.time()),
        }
        _write(rt)


def clear_topic_waiting(key: str) -> None:
    with _lock:
        rt = _read()
        wt = rt.get("waiting_topics") or {}
        if key in wt:
            wt.pop(key, None)
            rt["waiting_topics"] = wt
            _write(rt)


def get_waiting_topics() -> dict:
    with _lock:
        return dict(_read().get("waiting_topics") or {})


def set_pending_up(
    key: str,
    *,
    title: str,
    src_chat_id: int,
    topic_id: int,
    kind: str = "topic",
    have_media: int,
    need_media: int,
    next_schedule_at: int = 0,
    branch: str = "ads",
) -> None:
    with _lock:
        rt = _read()
        rt.setdefault("pending_up", {})[key] = {
            "title": title,
            "src_chat_id": src_chat_id,
            "topic_id": topic_id,
            "kind": kind,
            "have_media": have_media,
            "need_media": need_media,
            "notified_at": int(time.time()),
            "next_schedule_at": next_schedule_at,
            "branch": branch,
        }
        wt = rt.get("waiting_topics") or {}
        wt.pop(key, None)
        rt["waiting_topics"] = wt
        _write(rt)


def clear_pending_up(key: str) -> None:
    with _lock:
        rt = _read()
        pu = rt.get("pending_up") or {}
        if key in pu:
            pu.pop(key, None)
            rt["pending_up"] = pu
            _write(rt)


def clear_all_pending_up(*, log_msg: str = "") -> int:
    with _lock:
        rt = _read()
        pu = rt.get("pending_up") or {}
        n = len(pu)
        if n:
            rt["pending_up"] = {}
            _write(rt)
    if log_msg and n:
        append_log("info", log_msg)
    return n


def get_pending_up() -> dict:
    with _lock:
        return dict(_read().get("pending_up") or {})


def set_all_batch_preview(data: dict | None) -> None:
    with _lock:
        rt = _read()
        if data is None:
            rt.pop("all_batch_preview", None)
        else:
            rt["all_batch_preview"] = data
        _write(rt)


def get_all_batch_preview() -> dict | None:
    with _lock:
        return _read().get("all_batch_preview")
