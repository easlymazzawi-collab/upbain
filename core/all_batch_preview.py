"""Quét batch /all → runtime + đồng bộ chọn bài nhánh Up bài."""

from __future__ import annotations

import logging
import time
from typing import Any

from core.all_config import batch_signature, default_plain_included, merge_plain_task, plain_skip_last
from core.config_store import get_topic_source, load_auto_config
from core.runtime import pipeline_busy, set_all_batch_preview
from core.source_collector import collect_batch_from_topic

log = logging.getLogger("all_batch_preview")

POLL_INTERVAL_SEC = 15


def _all_topic_cfg(task: dict, src_chat: int, src_topic: int) -> dict:
    base = get_topic_source(src_chat, src_topic) or {"enabled": True}
    return {
        **base,
        "start_msg_id": task.get("start_msg_id") or base.get("start_msg_id"),
        "cursor_msg_id": task.get("cursor_msg_id") or base.get("cursor_msg_id"),
        "pin_mode": task.get("pin_mode") or base.get("pin_mode") or "latest",
        "start_link": task.get("start_link") or base.get("start_link"),
        "target_media_override": task.get("target_media_override") or base.get("target_media_override"),
        "_all_task_mode": True,
        "include_text_posts": task.get("include_text_posts", True),
    }


async def refresh_all_batch_preview(client) -> dict | None:
    cfg = load_auto_config()
    task = cfg.get("all_task") or {}
    src_chat = task.get("source_chat_id")
    src_topic = task.get("source_topic_id")
    if src_topic is None:
        src_topic = 0
    if not src_chat:
        set_all_batch_preview(None)
        return None

    tcfg = _all_topic_cfg(task, src_chat, src_topic)
    result = await collect_batch_from_topic(
        client, src_chat, src_topic, tcfg, cfg.get("global", {}), dry_run=True,
    )

    post_ids = [p.msg_id for p in result.posts]
    sig = batch_signature(post_ids)
    plain = cfg.get("plain_task") or {}
    if plain.get("batch_signature") != sig:
        merge_plain_task({
            "batch_signature": sig,
            "included_msg_ids": default_plain_included(post_ids, plain_skip_last(cfg)),
        })

    preview: dict[str, Any] = {
        "src_chat_id": src_chat,
        "topic_id": src_topic,
        "posts": [
            {"msg_id": p.msg_id, "media_count": p.media_count, "index": i + 1, "is_text": p.is_text}
            for i, p in enumerate(result.posts)
        ],
        "total_posts": len(result.posts),
        "total_media": result.total_media,
        "target_media": int(result.params.get("target_media") or 0),
        "target_ads": int(result.params.get("target_ads") or 0),
        "sufficient": bool(result.sufficient),
        "pinned_msg_id": result.pinned_msg_id,
        "cursor_msg_id": result.cursor_msg_id,
        "remaining_posts": result.remaining_posts,
        "remaining_media": result.remaining_media,
        "warn": result.warn,
        "all_task_mode": True,
        "updated_at": int(time.time()),
    }
    set_all_batch_preview(preview)
    return preview


async def all_batch_preview_loop(client) -> None:
    """Poll batch /all để web hiển thị real-time."""
    while True:
        try:
            if not pipeline_busy():
                cfg = load_auto_config()
                task = cfg.get("all_task") or {}
                if task.get("source_chat_id"):
                    await refresh_all_batch_preview(client)
        except Exception as e:
            log.warning("all batch preview: %s", e)
        await __import__("asyncio").sleep(POLL_INTERVAL_SEC)
