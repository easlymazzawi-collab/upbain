"""Ghim / bỏ ghim qua Telegram Bot API — bot admin trong nhóm nguồn."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from core.settings import pin_bot_token

log = logging.getLogger("pin_bot")


async def _bot_api(method: str, payload: dict) -> dict:
    token = pin_bot_token()
    if not token:
        raise RuntimeError("Chưa cấu hình Pin bot token trên web")

    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    import asyncio

    def _post() -> dict:
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode())
                if not body.get("ok"):
                    raise RuntimeError(body.get("description") or "Bot API lỗi")
                return body.get("result") or {}
        except urllib.error.HTTPError as e:
            err = e.read().decode() if e.fp else str(e)
            raise RuntimeError(f"HTTP {e.code}: {err}") from e

    return await asyncio.to_thread(_post)


def _thread_kw(topic_id: int | None) -> dict:
    tid = int(topic_id or 0)
    if tid != 0:
        return {"message_thread_id": tid}
    return {}


async def bot_unpin_message(chat_id: int, msg_id: int | None, topic_id: int | None = None) -> None:
    if not msg_id:
        return
    await _bot_api("unpinChatMessage", {
        "chat_id": chat_id,
        "message_id": int(msg_id),
        **_thread_kw(topic_id),
    })
    log.info("Bot unpin chat=%s msg=%s topic=%s", chat_id, msg_id, topic_id or 0)


async def bot_pin_message(
    chat_id: int,
    msg_id: int,
    topic_id: int | None = None,
    *,
    disable_notification: bool = True,
) -> None:
    await _bot_api("pinChatMessage", {
        "chat_id": chat_id,
        "message_id": int(msg_id),
        "disable_notification": disable_notification,
        **_thread_kw(topic_id),
    })
    log.info("Bot pin chat=%s msg=%s topic=%s", chat_id, msg_id, topic_id or 0)


async def bot_advance_topic_pin(
    chat_id: int,
    topic_id: int | None,
    old_pin_msg_id: int | None,
    new_pin_msg_id: int,
) -> None:
    """Bỏ ghim cũ → ghim bài tiếp theo (silent)."""
    try:
        await bot_unpin_message(chat_id, old_pin_msg_id, topic_id)
    except Exception as e:
        log.warning("bot unpin (bỏ qua) chat=%s msg=%s: %s", chat_id, old_pin_msg_id, e)
    await bot_pin_message(chat_id, new_pin_msg_id, topic_id)
