"""Hook tích hợp userbot sau up kênh."""

from __future__ import annotations

import logging

from research_platform.orchestrator import index_after_channel_forward

log = logging.getLogger("platform.hook")


async def after_auto_forward(
    *,
    src_chat_id: int,
    topic_id: int,
    branch: str,
    sequence: list[tuple[int, int]],
    atomic_posts: list | None = None,
    next_pin_msg_id: int | None = None,
    notify=None,
) -> None:
    try:
        await index_after_channel_forward(
            src_chat_id=src_chat_id,
            topic_id=topic_id,
            branch=branch,
            sequence=sequence,
            atomic_posts=atomic_posts,
            next_pin_msg_id=next_pin_msg_id,
            notify=notify,
        )
    except Exception as e:
        log.exception("platform after_auto_forward: %s", e)
