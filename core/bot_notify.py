"""Gửi thông báo qua Telegram Bot API (bot token cấu hình trên web)."""

import json
import logging
import urllib.error
import urllib.request

from core.settings import bot_token, notify_chat_id

log = logging.getLogger("bot_notify")


async def send_bot_notify_to(
    token: str,
    chat_id: int | str,
    text: str,
    parse_mode: str | None = None,
) -> bool:
    if not token or chat_id is None:
        return False

    payload: dict = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    import asyncio

    def _post():
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
                return bool(body.get("ok"))
        except urllib.error.HTTPError as e:
            err = e.read().decode() if e.fp else str(e)
            log.warning("bot notify HTTP %s: %s", e.code, err)
            return False
        except Exception as e:
            log.warning("bot notify fail: %s", e)
            return False

    return await asyncio.to_thread(_post)


async def send_bot_notify(text: str, parse_mode: str | None = None) -> bool:
    token = bot_token()
    chat_id = notify_chat_id()
    return await send_bot_notify_to(token, chat_id, text, parse_mode=parse_mode)
