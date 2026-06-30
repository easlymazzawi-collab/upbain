"""Parse link Telegram → chat_id, topic_id, msg_id."""

import re
from typing import Any

# https://t.me/c/1234567890/5/678  (forum topic)
# https://t.me/c/1234567890/678     (plain supergroup)
# https://t.me/username/678
_C_RE = re.compile(r"t\.me/c/(\d+)/(?:(\d+)/)?(\d+)", re.I)
_USER_RE = re.compile(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", re.I)


def parse_telegram_link(url: str) -> dict[str, Any] | None:
    url = (url or "").strip()
    if not url:
        return None

    m = _C_RE.search(url)
    if m:
        raw_chat = int(m.group(1))
        chat_id = int(f"-100{raw_chat}")
        if m.group(2):
            return {
                "src_chat_id": chat_id,
                "topic_id": int(m.group(2)),
                "msg_id": int(m.group(3)),
            }
        return {
            "src_chat_id": chat_id,
            "topic_id": None,
            "msg_id": int(m.group(3)),
        }

    m = _USER_RE.search(url)
    if m:
        return {
            "username": m.group(1),
            "msg_id": int(m.group(2)),
        }
    return None


def normalize_chat_id(raw: int | str) -> int:
    s = str(raw).strip()
    if s.startswith("-100"):
        return int(s)
    if s.lstrip("-").isdigit():
        n = int(s)
        if n > 0:
            return int(f"-100{n}")
        return n
    return int(s)
