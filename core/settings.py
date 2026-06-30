"""Dynamic settings — ưu tiên web config (auto_config.json), env chỉ bootstrap."""

import os

from core.config_store import load_auto_config

CHANNELS_FILE = "channels.json"


def _global() -> dict:
    return load_auto_config().get("global", {})


def api_id() -> int | None:
    v = _global().get("api_id")
    if v is not None:
        return int(v)
    e = os.getenv("API_ID")
    return int(e) if e else None


def api_hash() -> str | None:
    v = _global().get("api_hash")
    if v:
        return str(v)
    return os.getenv("API_HASH") or None


def intermediate_chat_id() -> int | None:
    v = _global().get("intermediate_chat")
    if v is not None:
        return int(v)
    e = os.getenv("INTERMEDIATE_CHAT")
    return int(e) if e else None


def ads_chat_id() -> int | None:
    v = _global().get("ads_chat")
    if v is not None:
        return int(v)
    e = os.getenv("ADS_CHAT")
    return int(e) if e else None


def web_port() -> int:
    v = _global().get("web_port")
    if v is not None:
        return int(v)
    e = os.getenv("WEB_PORT")
    return int(e) if e else 8080


def web_token() -> str:
    return str(_global().get("web_token") or os.getenv("WEB_TOKEN") or "")


def bot_token() -> str:
    return str(_global().get("bot_token") or "").strip()


def notify_chat_id() -> int | None:
    v = _global().get("notify_chat_id")
    if v is not None:
        return int(v)
    return None
