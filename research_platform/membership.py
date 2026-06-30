"""Force-join membership gate (clender pattern)."""

from __future__ import annotations

import logging
from typing import Any

from research_platform.config import load_platform_config

log = logging.getLogger("platform.membership")

_pending: dict[int, str] = {}


def set_pending(user_id: int, action: str) -> None:
    _pending[user_id] = action


def pop_pending(user_id: int) -> str | None:
    return _pending.pop(user_id, None)


async def check_membership(bot, user_id: int) -> tuple[bool, dict | None]:
    plat = load_platform_config()
    ch_id = plat.get("membership_channel_id")
    if not ch_id:
        return True, None
    try:
        member = await bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        status = getattr(member, "status", None)
        ok = status in ("member", "administrator", "creator")
        return ok, {"channel_id": ch_id, "username": plat.get("membership_channel_username") or ""}
    except Exception as e:
        log.warning("membership check: %s", e)
        return True, None


def membership_keyboard(channel_username: str = "", channel_id: int | None = None):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    url = ""
    if channel_username:
        un = channel_username.lstrip("@")
        url = f"https://t.me/{un}"
    elif channel_id:
        cid = str(channel_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        url = f"https://t.me/c/{cid}"
    buttons = []
    if url:
        buttons.append([InlineKeyboardButton(text="📢 Join kênh", url=url)])
    buttons.append([InlineKeyboardButton(text="✅ Đã join — kiểm tra", callback_data="mem_recheck")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def gate_or_prompt(bot, user_id: int, *, pending: str = "menu") -> bool:
    ok, info = await check_membership(bot, user_id)
    if ok:
        return True
    set_pending(user_id, pending)
    uname = (info or {}).get("username") or ""
    ch_id = (info or {}).get("channel_id")
    text = "⚠️ Vui lòng join kênh bắt buộc trước khi dùng bot."
    await bot.send_message(user_id, text, reply_markup=membership_keyboard(uname, ch_id))
    return False
