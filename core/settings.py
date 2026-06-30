"""Dynamic settings — chỉ đọc từ web config (data/auto_config.json)."""

from core.config_store import load_auto_config

CHANNELS_FILE = "channels.json"


def _global() -> dict:
    return load_auto_config().get("global", {})


def api_id() -> int | None:
    v = _global().get("api_id")
    return int(v) if v is not None else None


def api_hash() -> str | None:
    v = _global().get("api_hash")
    return str(v) if v else None


def intermediate_chat_id() -> int | None:
    v = _global().get("intermediate_chat")
    return int(v) if v is not None else None


def ads_chat_id() -> int | None:
    v = _global().get("ads_chat")
    return int(v) if v is not None else None


def web_port() -> int:
    v = _global().get("web_port")
    return int(v) if v is not None else 8080


def web_token() -> str:
    return str(_global().get("web_token") or "")


def bot_token() -> str:
    return str(_global().get("bot_token") or "").strip()


def notify_chat_id() -> int | None:
    v = _global().get("notify_chat_id")
    return int(v) if v is not None else None


def api_ready() -> bool:
    return bool(api_id() and api_hash())
