"""Chờ đủ bài — poll lại khi START + throttle thông báo."""

import asyncio
import logging
import time
from typing import Awaitable, Callable

from core.config_store import load_auto_config, topic_key
from core.runtime import append_log, clear_topic_waiting, get_runtime, set_topic_waiting
from core.settings import system_armed

log = logging.getLogger("stock_watcher")

RunTopicFn = Callable[..., Awaitable[bool]]
_last_wait_notify: dict[str, int] = {}
_NOTIFY_COOLDOWN = 1800  # 30 phút / topic


def require_full_batch() -> bool:
    return bool(load_auto_config().get("global", {}).get("require_full_batch", True))


def poll_interval_sec() -> int:
    v = load_auto_config().get("global", {}).get("stock_poll_interval_sec") or 300
    return max(60, int(v))


async def notify_wait(
    notify: Callable[..., Awaitable[None]],
    key: str,
    text: str,
    *,
    force: bool = False,
) -> None:
    now = int(time.time())
    if not force and now - _last_wait_notify.get(key, 0) < _NOTIFY_COOLDOWN:
        append_log("info", text.replace("<b>", "").replace("</b>", ""))
        return
    _last_wait_notify[key] = now
    await notify(text, parse_mode="HTML")


def mark_waiting(key: str, title: str, have: int, need: int) -> None:
    set_topic_waiting(key, title=title, have_media=have, need_media=need)


def mark_ready(key: str) -> None:
    clear_topic_waiting(key)
    _last_wait_notify.pop(key, None)


async def stock_poll_loop(run_topic: RunTopicFn) -> None:
    """Khi START + require_full_batch: poll topic đang chờ đủ bài."""
    running = False
    while True:
        interval = poll_interval_sec()
        try:
            if system_armed() and require_full_batch() and not running:
                waiting = get_runtime().get("waiting_topics") or {}
                if waiting:
                    cfg = load_auto_config()
                    by_key = {
                        topic_key(t.get("src_chat_id", 0), t.get("topic_id", 0)): t
                        for t in cfg.get("topic_sources", {}).values()
                    }
                    all_task = cfg.get("all_task") or {}
                    if all_task.get("enabled") and all_task.get("source_chat_id"):
                        ak = topic_key(all_task["source_chat_id"], all_task.get("source_topic_id") or 0)
                        if ak in waiting:
                            by_key[ak] = {
                                "src_chat_id": all_task["source_chat_id"],
                                "topic_id": all_task.get("source_topic_id"),
                                "topic_title": all_task.get("source_title") or "/all",
                                "enabled": True,
                                "_is_all": True,
                            }

                    running = True
                    for key in list(waiting.keys()):
                        t = by_key.get(key)
                        if not t or not t.get("enabled", True):
                            continue
                        sid, tid = t.get("src_chat_id"), t.get("topic_id")
                        title = t.get("topic_title") or ""
                        if not sid or not tid:
                            continue
                        try:
                            if t.get("_is_all"):
                                await run_topic("all_task", sid, tid, title)
                            else:
                                await run_topic("topic", sid, tid, title)
                        except Exception as e:
                            log.warning("stock poll %s: %s", key, e)
                        await asyncio.sleep(2)
                    running = False
        except Exception as e:
            log.warning("stock poll tick: %s", e)
            running = False
        await asyncio.sleep(interval)
