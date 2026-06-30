"""Unpin old + pin next content marker in forum topic."""

import logging

from pyrogram.errors import FloodWait, RPCError

log = logging.getLogger("pin_manager")


async def get_pinned_message_id(client, chat_id: int, topic_id: int | None) -> int | None:
    kw: dict = {}
    if topic_id and int(topic_id) != 0:
        kw["reply_to_message_id"] = int(topic_id)
    try:
        async for msg in client.search_messages(
            chat_id,
            query="",
            filter="pinned",
            limit=5,
            **kw,
        ):
            if msg and not msg.empty:
                return msg.id
    except TypeError:
        try:
            from pyrogram import enums
            async for msg in client.search_messages(
                chat_id,
                query="",
                filter=enums.MessagesFilter.PINNED,
                limit=5,
                **kw,
            ):
                if msg and not msg.empty:
                    return msg.id
        except Exception as e:
            log.warning("search pinned fallback fail: %s", e)
    except Exception as e:
        log.warning("get_pinned_message_id: %s", e)
    return None


async def unpin_message(client, chat_id: int, msg_id: int | None) -> None:
    if not msg_id:
        return
    try:
        await client.unpin_chat_message(chat_id, msg_id)
        log.info("Unpinned chat=%s msg=%s", chat_id, msg_id)
    except FloodWait as e:
        import asyncio
        await asyncio.sleep(e.value + 1)
        await client.unpin_chat_message(chat_id, msg_id)
    except RPCError as e:
        log.warning("unpin fail chat=%s msg=%s: %s", chat_id, msg_id, e)


async def pin_message(client, chat_id: int, msg_id: int) -> None:
    try:
        await client.pin_chat_message(chat_id, msg_id, disable_notification=True)
        log.info("Pinned chat=%s msg=%s", chat_id, msg_id)
    except FloodWait as e:
        import asyncio
        await asyncio.sleep(e.value + 1)
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
    await unpin_message(client, chat_id, old_pin_msg_id)
    await pin_message(client, chat_id, new_pin_msg_id)
