"""Orchestrator queue — tuần tự 10 nguồn/bot."""

from __future__ import annotations

import asyncio
import logging

from research_platform.config import get_bot_for_source, list_bots_config, load_platform_config

log = logging.getLogger("platform.run_queue")

_queue_lock = asyncio.Lock()
_running = False


async def run_platform_queue(
    run_topic_fn,
    *,
    notify=None,
) -> list[dict]:
    """
    Chạy tuần tự từng bot đã cấu hình (queue_order).
    run_topic_fn(src_chat_id, topic_id, branch, topic_title) -> bool
    """
    global _running
    plat = load_platform_config()
    if not plat.get("enabled") or not plat.get("orchestrator_running", True):
        return []

    bots = sorted(
        [b for b in list_bots_config(plat) if b.get("enabled", True)],
        key=lambda x: x.get("queue_order", 0),
    )
    if not bots:
        return []

    results = []
    async with _queue_lock:
        if _running:
            log.info("platform queue already running")
            return []
        _running = True
        try:
            for i, b in enumerate(bots, 1):
                src = b.get("source_forum_id")
                topic = b.get("source_topic_id")
                if not src or topic is None:
                    continue
                branch = b.get("branch", "ads")
                title = b.get("username") or f"bot{i}"
                if notify:
                    await notify(f"▶️ Platform queue {i}/{len(bots)} · @{title}")
                try:
                    ok = await run_topic_fn(int(src), int(topic), branch, title)
                    results.append({"bot": title, "ok": ok})
                except Exception as e:
                    log.exception("queue bot %s: %s", title, e)
                    results.append({"bot": title, "ok": False, "error": str(e)})
                await asyncio.sleep(2)
        finally:
            _running = False
    return results


def queue_status() -> dict:
    return {"running": _running, "bots": len(list_bots_config())}
