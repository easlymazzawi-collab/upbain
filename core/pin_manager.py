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


def _pinned_filter():
    try:
        from pyrogram import enums
        return enums.MessagesFilter.PINNED
    except Exception:
        return "pinned"


def _use_pin_bot() -> bool:
    from core.settings import pin_bot_token
    return bool(pin_bot_token())


async def get_pinned_message_id(client, chat_id: int, topic_id: int | None) -> int | None:
    """Đọc tin ghim — cần userbot (Bot API không liệt kê ghim theo topic)."""
    kw: dict = {}
    if topic_id and int(topic_id) != 0:
        kw["reply_to_message_id"] = int(topic_id)
    filt = _pinned_filter()
    try:
        async for msg in client.search_messages(
            chat_id,
            query="",
            filter=filt,
            limit=5,
            **kw,
        ):
            if msg and not msg.empty:
                return msg.id
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
        await bot_unpin_message(chat_id, msg_id, topic_id)
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
    if _use_pin_bot():
        from core.pin_bot import bot_pin_message
        await bot_pin_message(chat_id, msg_id, topic_id)
        return
    try:
        await client.pin_chat_message(chat_id, msg_id, disable_notification=True)
        log.info("Pinned chat=%s msg=%s", chat_id, msg_id)
    except FloodWait as e:
        await asyncio.sleep(_flood_wait_seconds(e) + 1)
        await client.pin_chat_message(chat_id, msg_id, disable_notification=True)
    except RPCError as e:
        log.warning("pin fail chat=%s msg=%s: %s", chat_id, msg_id, e)
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
        await bot_advance_topic_pin(chat_id, tid, old_pin_msg_id, new_pin_msg_id)
        return
    await unpin_message(client, chat_id, old_pin_msg_id, tid)
    await pin_message(client, chat_id, new_pin_msg_id, tid)
