"""Orchestrator — ghi index sau up kênh, thông báo admin."""

from __future__ import annotations

import logging
from typing import Any

from core.link_parser import msg_link
from research_platform.ads import apply_aliases_to_items
from research_platform.archive_index import (
    find_bot_by_source,
    get_day_by_label,
    get_or_create_day,
    publish_day,
    replace_day_items,
    sync_bot_from_config,
)
from research_platform.config import get_bot_for_source, layer_enabled, list_bots_config, load_platform_config
from research_platform.dates import topic_label_vn, today_vn

log = logging.getLogger("platform.orchestrator")


def _ads_chat_id() -> int | None:
    try:
        from core.settings import ads_chat_id
        return ads_chat_id()
    except Exception:
        return None


def sequence_to_items(
    sequence: list[tuple[int, int]],
    *,
    content_chat: int,
    atomic_posts: list | None = None,
    ads_chat: int | None = None,
) -> list[dict[str, Any]]:
    """Chuyển final_sequence thành day_items."""
    ads_chat = ads_chat or _ads_chat_id()
    post_map: dict[int, dict] = {}
    if atomic_posts:
        for p in atomic_posts:
            mid = getattr(p, "msg_id", None) or (p.get("msg_id") if isinstance(p, dict) else None)
            if mid:
                album = getattr(p, "album_ids", None) or (p.get("album_ids") if isinstance(p, dict) else [])
                post_map[mid] = {
                    "album_msg_ids": album or [mid],
                    "is_album": bool(getattr(p, "is_album", False) or (isinstance(p, dict) and p.get("is_album"))),
                }

    items: list[dict[str, Any]] = []
    for src_chat, msg_id in sequence:
        is_ads = ads_chat is not None and int(src_chat) == int(ads_chat)
        meta = post_map.get(msg_id, {})
        items.append({
            "src_chat_id": int(src_chat),
            "src_msg_id": int(msg_id),
            "item_type": "ads" if is_ads else "content",
            "ads_alias": None,
            "album_msg_ids": meta.get("album_msg_ids") or [msg_id],
        })
    return items


async def index_after_channel_forward(
    *,
    src_chat_id: int,
    topic_id: int,
    branch: str,
    sequence: list[tuple[int, int]],
    atomic_posts: list | None = None,
    next_pin_msg_id: int | None = None,
    notify=None,
) -> dict | None:
    """
    Ghi index sau khi up kênh xong.
    Trả metadata day nếu thành công.
    """
    plat = load_platform_config()
    if not plat.get("enabled"):
        return None

    bot_cfg = get_bot_for_source(src_chat_id, topic_id, branch, plat)
    bot_row = find_bot_by_source(src_chat_id, topic_id, branch)
    if bot_cfg and not bot_row:
        bot_id = sync_bot_from_config(bot_cfg)
    elif bot_row:
        bot_id = bot_row["id"]
        bot_cfg = bot_cfg or {}
    else:
        legacy = plat.get("bot") or {}
        if (
            legacy.get("source_forum_id") == src_chat_id
            and legacy.get("source_topic_id") == topic_id
        ):
            bot_id = sync_bot_from_config(legacy)
            bot_cfg = legacy
        else:
            log.info("platform: no bot mapped for %s:%s branch=%s", src_chat_id, topic_id, branch)
            return None

    if not layer_enabled("archive_index", bot_cfg):
        return None
    if not sequence:
        return None

    day = get_or_create_day(bot_id)
    content_chat = src_chat_id
    items = sequence_to_items(
        sequence,
        content_chat=content_chat,
        atomic_posts=atomic_posts,
    )
    items = apply_aliases_to_items(items)
    count = replace_day_items(day["id"], items, channel_sent=True)
    if next_pin_msg_id:
        from research_platform.db import connect
        with connect() as conn:
            conn.execute(
                "UPDATE days SET next_src_msg_id=? WHERE id=?",
                (next_pin_msg_id, day["id"]),
            )

    # Recheck ads sau index
    try:
        from research_platform.ads import recheck_ads_aliases_for_day
        recheck_ads_aliases_for_day(day["id"])
    except Exception as e:
        log.warning("recheck ads: %s", e)

    publish_day(day["id"])
    day_label = topic_label_vn(today_vn())
    username = bot_cfg.get("username") or ""

    lines = [
        f"📚 <b>Index ngày</b> · {day_label}",
        f"Bot: @{username}" if username else "Bot delivery",
        f"📦 {count} items đã ghi index",
    ]
    if next_pin_msg_id:
        lines.append(f"⏭ Bài kế: {msg_link(src_chat_id, topic_id, next_pin_msg_id, label='link')}")
    if username:
        lines.append(f"🤖 User: /day {day_label}")

    text = "\n".join(lines)
    await _send_admin_notify(text, notify=notify)

    log.info("platform: indexed day_id=%s items=%s", day["id"], count)
    return {"day_id": day["id"], "bot_id": bot_id, "items": count, "topic_label": day_label}


async def _send_admin_notify(text: str, *, notify=None) -> None:
    plat = load_platform_config()
    group_id = plat.get("admin_notify_group_id")
    if group_id:
        try:
            from core.bot_notify import send_bot_notify_to
            token = (plat.get("bot") or {}).get("token") or ""
            from core.settings import bot_token
            token = token or bot_token()
            await send_bot_notify_to(token, group_id, text, parse_mode="HTML")
            return
        except Exception as e:
            log.warning("admin notify group: %s", e)
    if notify:
        await notify(text)


def get_day_for_bot_label(topic_label: str) -> dict | None:
    plat = load_platform_config()
    bot_cfg = plat.get("bot") or {}
    bot_id = sync_bot_from_config(bot_cfg)
    return get_day_by_label(bot_id, topic_label)
