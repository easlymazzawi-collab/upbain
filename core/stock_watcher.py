"""Chờ đủ bài — poll kiểm tra, offer /upngay, không auto-up."""

import asyncio
import logging
from typing import Awaitable, Callable

from core.config_store import load_auto_config, topic_key
from core.runtime import get_runtime, pipeline_busy
from core.settings import system_armed
from core.up_confirm import expire_pending_near_schedule, is_pending

log = logging.getLogger("stock_watcher")

RunTopicFn = Callable[..., Awaitable[bool]]
_last_wait_notify: dict[str, int] = {}
_NOTIFY_COOLDOWN = 1800


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
    import time
    from core.runtime import append_log

    now = int(time.time())
    if not force and now - _last_wait_notify.get(key, 0) < _NOTIFY_COOLDOWN:
        append_log("info", text.replace("<b>", "").replace("</b>", ""))
        return
    _last_wait_notify[key] = now
    await notify(text, parse_mode="HTML")


def mark_waiting(key: str, title: str, have: int, need: int) -> None:
    from core.runtime import set_topic_waiting
    set_topic_waiting(key, title=title, have_media=have, need_media=need)


def mark_ready(key: str) -> None:
    from core.runtime import clear_topic_waiting
    clear_topic_waiting(key)
    _last_wait_notify.pop(key, None)


async def stock_poll_loop(run_topic: RunTopicFn) -> None:
    """Poll topic thiếu bài; khi đủ → offer /upngay (không up ngay)."""
    running = False
    while True:
        interval = poll_interval_sec()
        try:
            expire_pending_near_schedule(silent=True)

            if system_armed() and require_full_batch() and not running and not pipeline_busy():
                waiting = get_runtime().get("waiting_topics") or {}
                if waiting:
                    cfg = load_auto_config()
                    by_key = {}
                    from core.channel_store import BRANCH_ADS, BRANCH_PLAIN
                    for branch, sk in (
                        (BRANCH_ADS, "topic_sources"),
                        (BRANCH_PLAIN, "plain_topic_sources"),
                    ):
                        for t in cfg.get(sk, {}).values():
                            k = topic_key(t.get("src_chat_id", 0), t.get("topic_id") or 0)
                            entry = dict(t)
                            entry["_branch"] = branch
                            by_key[k] = entry
                    all_task = cfg.get("all_task") or {}
                    plain_task = cfg.get("plain_task") or {}
                    if (
                        (all_task.get("enabled") or plain_task.get("enabled"))
                        and all_task.get("source_chat_id")
                        and all_task.get("require_full_batch", False)
                    ):
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
                        if is_pending(key):
                            continue
                        t = by_key.get(key)
                        if not t or not t.get("enabled", True):
                            continue
                        sid, tid = t.get("src_chat_id"), t.get("topic_id")
                        title = t.get("topic_title") or ""
                        if not sid:
                            continue
                        if tid is None:
                            tid = 0
                        try:
                            br = t.get("_branch", "ads")
                            if t.get("_is_all"):
                                await run_topic("all_task", sid, tid, title, check_only=True)
                            else:
                                await run_topic("topic", sid, tid, title, check_only=True, branch=br)
                        except Exception as e:
                            log.warning("stock poll %s: %s", key, e)
                        await asyncio.sleep(2)
                    running = False
        except Exception as e:
            log.warning("stock poll tick: %s", e)
            running = False
        await asyncio.sleep(interval)
