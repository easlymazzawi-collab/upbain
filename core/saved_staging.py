"""Stage batch nguồn → Saved Messages một lần, rồi up kênh từ Saved (giống làm tay)."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from pyrogram.raw import functions, types

if TYPE_CHECKING:
    from core.source_collector import AtomicPost

log = logging.getLogger("saved_staging")

STAGE_DELAY_SEC = 2.5
STAGE_JITTER_SEC = 0.6
SAVED_CHAT = "me"


def auto_stage_via_saved() -> bool:
    from core.config_store import load_auto_config
    g = load_auto_config().get("global", {})
    return g.get("auto_stage_via_saved", True) is not False


def _first_saved_id(updates) -> int | None:
    for upd in getattr(updates, "updates", []) or []:
        msg = None
        if isinstance(upd, types.UpdateNewMessage):
            msg = upd.message
        elif isinstance(upd, types.UpdateNewChannelMessage):
            msg = upd.message
        if msg is not None and getattr(msg, "id", None):
            return int(msg.id)
    return None


async def stage_posts_to_saved(
    client,
    src_chat: int,
    posts: list[AtomicPost],
    *,
    acquire=None,
    delay_sec: float = STAGE_DELAY_SEC,
) -> list[int]:
    """
    Forward mỗi bài/album từ chat nguồn → Saved Messages (không get_messages).
    Trả list msg id trên Saved — dùng cho up nhiều kênh mà không gọi lại nguồn.
    """
    if not posts:
        return []

    from_peer = await client.resolve_peer(src_chat)
    to_peer = await client.resolve_peer(SAVED_CHAT)
    saved_ids: list[int] = []
    total = len(posts)

    for i, post in enumerate(posts, 1):
        ids = list(post.album_ids) if post.album_ids else [int(post.msg_id)]
        try:
            if acquire:
                await acquire()
            log.info(
                "STAGE %s/%s: src msg %s (%s tin) → Saved Messages",
                i, total, post.msg_id, len(ids),
            )
            r = await client.invoke(
                functions.messages.ForwardMessages(
                    from_peer=from_peer,
                    to_peer=to_peer,
                    id=ids,
                    drop_author=True,
                    random_id=[random.randint(0, 2**63) for _ in ids],
                    silent=True,
                ),
                sleep_threshold=60,
            )
            saved_id = _first_saved_id(r)
            if not saved_id:
                async for msg in client.get_chat_history(SAVED_CHAT, limit=3):
                    if msg and not msg.empty and not msg.service:
                        saved_id = msg.id
                        break
            if saved_id:
                saved_ids.append(saved_id)
            else:
                log.warning("STAGE: không lấy được id Saved sau forward src=%s", post.msg_id)
        except Exception as e:
            log.warning("STAGE fail src msg %s: %s", post.msg_id, e)
        if i < total:
            await asyncio.sleep(delay_sec + random.uniform(0, STAGE_JITTER_SEC))

    return saved_ids


async def maybe_stage_slot_data(
    client,
    slot_data: dict,
    *,
    acquire=None,
) -> dict:
    """Nếu bật auto_stage_via_saved — thay content bằng bản trên Saved."""
    if not auto_stage_via_saved():
        return slot_data

    posts = slot_data.get("_atomic_posts")
    src = slot_data.get("content_chat")
    if not posts or src in (None, SAVED_CHAT, "me"):
        return slot_data

    try:
        src_id = int(src)
    except (TypeError, ValueError):
        return slot_data

    from core.source_collector import AtomicPost

    if posts and not isinstance(posts[0], AtomicPost):
        posts = [
            AtomicPost(msg_id=int(m), media_count=0)
            for m in (slot_data.get("content_msgs") or [])
        ]

    saved = await stage_posts_to_saved(client, src_id, posts, acquire=acquire)
    if len(saved) != len(posts):
        log.warning(
            "STAGE: chỉ %s/%s bài — giữ forward trực tiếp từ nguồn",
            len(saved), len(posts),
        )
        return slot_data

    log.info("STAGE: xong %s bài trên Saved — up kênh sẽ đọc từ Saved (như làm tay)", len(saved))
    out = dict(slot_data)
    out["content_msgs"] = saved
    out["content_chat"] = SAVED_CHAT
    out["_staged_from_chat"] = src_id
    out["topic_src_id"] = src_id
    out["_stage_id_map"] = {
        int(post.msg_id): int(sid) for post, sid in zip(posts, saved)
    }
    return out
