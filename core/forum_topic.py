"""Forum topic helpers for Pyrogram 2.0.x (no reply_to_message_id on high-level API)."""

from __future__ import annotations

from typing import AsyncIterator

from pyrogram import raw, utils


def norm_topic_id(topic_id: int | None) -> int:
    return 0 if topic_id is None else int(topic_id)


def is_forum_topic(topic_id: int | None) -> bool:
    return norm_topic_id(topic_id) != 0


async def iter_topic_history(
    client,
    chat_id: int,
    topic_id: int | None,
    *,
    limit: int = 500,
) -> AsyncIterator:
    """Messages in a forum topic (or whole chat when topic_id=0), newest first."""
    tid = norm_topic_id(topic_id)
    if not is_forum_topic(tid):
        async for msg in client.get_chat_history(chat_id, limit=limit):
            yield msg
        return

    n = 0
    async for msg in client.get_discussion_replies(chat_id, tid, limit=limit):
        yield msg
        n += 1
        if limit and n >= limit:
            return


async def search_topic_pinned_message_id(
    client,
    chat_id: int,
    topic_id: int | None,
    *,
    limit: int = 5,
) -> int | None:
    """Pinned message id inside a forum topic, or chat-wide when topic_id=0."""
    tid = norm_topic_id(topic_id)
    filt = raw.types.InputMessagesFilterPinned()
    peer = await client.resolve_peer(chat_id)
    kwargs: dict = {}
    if is_forum_topic(tid):
        kwargs["top_msg_id"] = tid

    r = await client.invoke(
        raw.functions.messages.Search(
            peer=peer,
            q="",
            filter=filt,
            min_date=0,
            max_date=0,
            offset_id=0,
            add_offset=0,
            limit=max(1, min(limit, 100)),
            max_id=0,
            min_id=0,
            hash=0,
            **kwargs,
        ),
        sleep_threshold=60,
    )
    messages = await utils.parse_messages(client, r, replies=0)
    for msg in messages or []:
        if msg and not msg.empty:
            return msg.id
    return None
