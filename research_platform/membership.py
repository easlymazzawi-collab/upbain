"""Force-join membership — port clender/bot/membership.py (cache + pending)."""

from __future__ import annotations

import logging
import time

from research_platform.config import load_platform_config

log = logging.getLogger("research_platform.membership")

_DEFAULT_CHECK_SEC = 300
_NON_MEMBER_TTL = 60
_cache: dict[int, dict] = {}
_pending: dict[int, str] = {}


def _get_check_sec() -> int:
    plat = load_platform_config()
    try:
        return int(plat.get("force_join_check_sec") or _DEFAULT_CHECK_SEC)
    except (TypeError, ValueError):
        return _DEFAULT_CHECK_SEC


def _get_join_message() -> str:
    plat = load_platform_config()
    return plat.get("force_join_message") or (
        "🔒 Bạn cần tham gia kênh để dùng bot.\n"
        "Nhấn Join → bấm ✅ Kiểm tra lại."
    )


def _cached_ok(user_id: int) -> bool | None:
    entry = _cache.get(user_id)
    if not entry:
        return None
    ttl = _get_check_sec() if entry["ok"] else _NON_MEMBER_TTL
    if time.time() - entry["ts"] < ttl:
        return entry["ok"]
    return None


def _set_cache(user_id: int, ok: bool) -> None:
    _cache[user_id] = {"ok": ok, "ts": time.time()}


def invalidate(user_id: int) -> None:
    _cache.pop(user_id, None)


def set_pending(user_id: int, action: str) -> None:
    _pending[user_id] = action


def pop_pending(user_id: int) -> str | None:
    return _pending.pop(user_id, None)


async def is_member(bot, user_id: int, channel) -> bool:
    """Fail-open nếu cấu hình sai (clender pattern)."""
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        status = getattr(member, "status", None)
        ok = status in ("member", "administrator", "creator")
        if not ok:
            log.info("membership FAIL user=%s channel=%s status=%s", user_id, channel, status)
        return ok
    except Exception as e:
        msg = str(e).lower()
        if "not found" in msg or "invalid" in msg or "no rights" in msg:
            log.warning("membership config error channel=%s: %s — fail-open", channel, e)
            return True
        log.warning("is_member: %s — fail-open", e)
        return True


async def check_user(bot, user_id: int) -> bool:
    plat = load_platform_config()
    ch = plat.get("membership_channel_id")
    if not ch:
        return True
    cached = _cached_ok(user_id)
    if cached is not None:
        return cached
    ok = await is_member(bot, user_id, ch)
    _set_cache(user_id, ok)
    return ok


async def check_membership(bot, user_id: int) -> tuple[bool, str | None]:
    """API tương thích handler — trả (ok, channel)."""
    plat = load_platform_config()
    ch = plat.get("membership_channel_id")
    if not ch:
        return True, None
    ok = await check_user(bot, user_id)
    return ok, str(ch) if ch else None


def membership_keyboard(channel_username: str = "", channel_id: int | None = None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    url = ""
    if channel_username:
        url = f"https://t.me/{channel_username.lstrip('@')}"
    buttons = []
    if url:
        buttons.append([InlineKeyboardButton(text="📢 Join kênh", url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Đã join — kiểm tra", callback_data="mem_recheck")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def gate_or_prompt(bot, user_id: int, *, pending: str = "menu") -> bool:
    ok = await check_user(bot, user_id)
    if ok:
        return True
    set_pending(user_id, pending)
    plat = load_platform_config()
    await bot.send_message(
        user_id,
        _get_join_message(),
        reply_markup=membership_keyboard(
            plat.get("membership_channel_username") or "",
            plat.get("membership_channel_id"),
        ),
    )
    return False
