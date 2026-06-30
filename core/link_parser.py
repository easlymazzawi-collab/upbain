"""Parse link Telegram → chat_id, topic_id, msg_id."""

import re
from typing import Any

# https://t.me/c/1234567890/5/678  (forum topic)
# https://t.me/c/1234567890/678     (plain supergroup)
# https://t.me/username/678
_C_RE = re.compile(r"t\.me/c/(\d+)/(?:(\d+)/)?(\d+)", re.I)
_USER_RE = re.compile(r"t\.me/([a-zA-Z0-9_]+)/(\d+)", re.I)
_CHAT_TOPIC_RE = re.compile(r"^(-?\d+)\s*[:/]\s*(\d+)(?:\s*[:/]\s*(\d+))?$")
_CHAT_ONLY_RE = re.compile(r"^-?\d+$")


def parse_source_link(raw: str) -> dict[str, Any] | None:
    """
    Link nguồn /all — forum topic hoặc supergroup thường:
      https://t.me/c/123/5/678   (forum)
      https://t.me/c/123/678     (supergroup — chỉ cần chat + msg)
      -1001234567890             (chỉ chat ID)
      -1001234567890:5           (chat + topic forum)
    """
    raw = (raw or "").strip()
    if not raw:
        return None

    if "t.me" in raw or raw.startswith("http"):
        p = parse_telegram_link(raw)
        if not p or not p.get("src_chat_id"):
            return None
        out: dict[str, Any] = {
            "src_chat_id": int(p["src_chat_id"]),
            "topic_id": int(p["topic_id"]) if p.get("topic_id") is not None else 0,
        }
        if p.get("msg_id"):
            out["msg_id"] = int(p["msg_id"])
        return out

    m = _CHAT_TOPIC_RE.match(raw)
    if m:
        out = {
            "src_chat_id": normalize_chat_id(m.group(1)),
            "topic_id": int(m.group(2)),
        }
        if m.group(3):
            out["msg_id"] = int(m.group(3))
        return out

    if _CHAT_ONLY_RE.match(raw):
        return {"src_chat_id": normalize_chat_id(raw), "topic_id": 0}

    return None


def source_link_error(parsed: dict[str, Any] | None, raw: str) -> str | None:
    if not raw.strip():
        return "Chưa có link nguồn."
    if not parsed or not parsed.get("src_chat_id"):
        return (
            "Link không hợp lệ. Dán link Telegram "
            "(https://t.me/c/.../678) hoặc chat ID (-100...)"
        )
    return None


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


def escape_html(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def chat_id_to_tme_raw(chat_id: int) -> str:
    """-1001234567890 → 1234567890 for t.me/c/ URLs."""
    s = str(chat_id).strip()
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s


def msg_link(chat_id: int, topic_id: int | None = None, msg_id: int | None = None, label: str | None = None) -> str:
    """HTML anchor t.me/c/... cho bot notify."""
    raw = chat_id_to_tme_raw(chat_id)
    if topic_id is not None and msg_id is not None:
        url = f"https://t.me/c/{raw}/{topic_id}/{msg_id}"
        text = label or f"msg {msg_id}"
    elif msg_id is not None:
        url = f"https://t.me/c/{raw}/{msg_id}"
        text = label or f"msg {msg_id}"
    elif topic_id is not None:
        url = f"https://t.me/c/{raw}/{topic_id}"
        text = label or f"topic {topic_id}"
    else:
        url = f"https://t.me/c/{raw}"
        text = label or "chat"
    return f'<a href="{url}">{escape_html(text)}</a>'


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
