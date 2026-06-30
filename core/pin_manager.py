"""Unpin old + pin next content marker in forum topic (Bot API hoặc userbot)."""

import asyncio
import logging

from pyrogram.errors import FloodWait, RPCError

log = logging.getLogger("pin_manager")


def _flood_wait_seconds(err: BaseException) -> int:
    for attr in ("value", "x", "seconds"):
        v = getattr(err, attr, None)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                pass
    return 3


def _use_pin_bot() -> bool:
    from core.settings import pin_bot_token
    return bool(pin_bot_token())


async def get_pinned_message_id(client, chat_id: int, topic_id: int | None) -> int | None:
    """Đọc tin ghim — cần userbot (Bot API không liệt kê ghim theo topic)."""
    from core.forum_topic import search_topic_pinned_message_id

    try:
        pinned_id = await search_topic_pinned_message_id(client, chat_id, topic_id, limit=5)
        if pinned_id:
            return pinned_id
    except FloodWait as e:
        await asyncio.sleep(_flood_wait_seconds(e) + 1)
        return await get_pinned_message_id(client, chat_id, topic_id)
    except Exception as e:
        log.warning("get_pinned_message_id: %s", e)
    return None


async def unpin_message(
    client,
    chat_id: int,
    msg_id: int | None,
    topic_id: int | None = None,
) -> None:
    if not msg_id:
        return
    if _use_pin_bot():
        from core.pin_bot import bot_unpin_message
        try:
            await bot_unpin_message(chat_id, msg_id, topic_id)
        except Exception as e:
            log.warning("bot unpin fail chat=%s msg=%s: %s", chat_id, msg_id, e)
        return
    try:
        await client.unpin_chat_message(chat_id, msg_id)
        log.info("Unpinned chat=%s msg=%s", chat_id, msg_id)
    except FloodWait as e:
        await asyncio.sleep(_flood_wait_seconds(e) + 1)
        await client.unpin_chat_message(chat_id, msg_id)
    except RPCError as e:
        log.warning("unpin fail chat=%s msg=%s: %s", chat_id, msg_id, e)


async def pin_message(
    client,
    chat_id: int,
    msg_id: int,
    topic_id: int | None = None,
) -> None:
    last_err: Exception | None = None
    if _use_pin_bot():
        from core.pin_bot import bot_pin_message
        try:
            await bot_pin_message(chat_id, msg_id, topic_id)
            return
        except Exception as e:
            last_err = e
            log.warning("bot pin fail chat=%s msg=%s — thử userbot: %s", chat_id, msg_id, e)
    try:
        await client.pin_chat_message(chat_id, msg_id, disable_notification=True)
        log.info("Pinned chat=%s msg=%s topic=%s", chat_id, msg_id, topic_id or 0)
    except FloodWait as e:
        await asyncio.sleep(_flood_wait_seconds(e) + 1)
        await client.pin_chat_message(chat_id, msg_id, disable_notification=True)
    except RPCError as e:
        log.warning("pin fail chat=%s msg=%s: %s", chat_id, msg_id, e)
        if last_err:
            raise RuntimeError(f"Bot: {last_err}; userbot: {e}") from e
        raise


async def advance_topic_pin(
    client,
    chat_id: int,
    topic_id: int,
    old_pin_msg_id: int | None,
    new_pin_msg_id: int,
) -> None:
    """Unpin marker cũ, ghim bài tiếp theo chưa up."""
    tid = int(topic_id) if topic_id is not None else 0
    if _use_pin_bot():
        from core.pin_bot import bot_advance_topic_pin
        try:
            await bot_advance_topic_pin(chat_id, tid, old_pin_msg_id, new_pin_msg_id)
            return
        except Exception as e:
            log.warning("bot advance pin fail — thử userbot: %s", e)
    await unpin_message(client, chat_id, old_pin_msg_id, tid)
    await pin_message(client, chat_id, new_pin_msg_id, tid)
