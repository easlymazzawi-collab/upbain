"""
Collect atomic posts from forum topic starting at pinned message.
Rule: bài/album không tách — lấy nguyên cả bài; ghép đủ target media (có thể vượt 1 bài).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from core.inventory import check_low_stock, update_after_scan
from core.pin_manager import get_pinned_message_id
from core.topic_parser import resolve_batch_params

log = logging.getLogger("source_collector")


def _norm_topic_id(topic_id: int | None) -> int:
    return 0 if topic_id is None else int(topic_id)


def _forum_topic(topic_id: int | None) -> bool:
    return _norm_topic_id(topic_id) != 0


def _history_kw(topic_id: int | None) -> dict:
    if _forum_topic(topic_id):
        return {"reply_to_message_id": _norm_topic_id(topic_id)}
    return {}


@dataclass
class AtomicPost:
    msg_id: int
    media_count: int
    is_album: bool = False
    album_ids: list[int] = field(default_factory=list)


@dataclass
class CollectResult:
    posts: list[AtomicPost]
    total_media: int
    total_posts: int
    pinned_msg_id: int | None
    next_pin_msg_id: int | None
    pinned_text: str | None
    params: dict[str, Any]
    remaining_posts: int
    remaining_media: int
    cursor_msg_id: int | None = None
    sufficient: bool = False
    warn: str | None = None


async def _media_count(client, chat_id: int, msg) -> tuple[int, list[int]]:
    if msg.media_group_id:
        try:
            album = await client.get_media_group(chat_id, msg.id)
            ids = [m.id for m in album if m and not m.empty]
            return max(1, len(ids)), ids
        except Exception as e:
            log.warning("album fetch fail %s: %s", msg.id, e)
            return 1, [msg.id]
    if msg.media:
        return 1, [msg.id]
    return 0, [msg.id]


async def _find_oldest_media_cursor(client, chat_id: int, limit: int = 500) -> int | None:
    """Supergroup không ghim — lấy tin media cũ nhất trong cửa sổ quét."""
    ids: list[int] = []
    seen_groups: set[str] = set()
    async for msg in client.get_chat_history(chat_id, limit=limit):
        if msg.empty or msg.service:
            continue
        if msg.media_group_id:
            if msg.media_group_id in seen_groups:
                continue
            seen_groups.add(msg.media_group_id)
        mc, _ = await _media_count(client, chat_id, msg)
        if mc > 0:
            ids.append(msg.id)
    return min(ids) if ids else None


async def _iter_topic_posts_from(client, chat_id: int, topic_id: int | None, start_msg_id: int):
    """Messages from cursor (forum topic or whole supergroup), oldest→newest."""
    seen_groups: set[str] = set()
    candidates = []

    async for msg in client.get_chat_history(chat_id, limit=500, **_history_kw(topic_id)):
        if msg.empty or msg.service:
            continue
        if start_msg_id > 0 and msg.id < start_msg_id:
            break
        if msg.media_group_id:
            gid = msg.media_group_id
            if gid in seen_groups:
                continue
            seen_groups.add(gid)
        mc, _ = await _media_count(client, chat_id, msg)
        if mc <= 0:
            continue
        candidates.append(msg)

    if not candidates and start_msg_id:
        try:
            msg = await client.get_messages(chat_id, start_msg_id)
            if msg and not msg.empty and not msg.service:
                mc, _ = await _media_count(client, chat_id, msg)
                if mc > 0:
                    candidates.append(msg)
        except Exception as e:
            log.warning("get start msg %s: %s", start_msg_id, e)

    candidates.sort(key=lambda m: m.id)
    seen_groups.clear()
    for msg in candidates:
        if msg.media_group_id:
            if msg.media_group_id in seen_groups:
                continue
            seen_groups.add(msg.media_group_id)
        yield msg


async def _count_remaining(client, chat_id: int, topic_id: int | None, from_msg_id: int) -> tuple[int, int]:
    posts = 0
    media = 0
    seen_groups: set[str] = set()
    async for msg in client.get_chat_history(chat_id, limit=1000, **_history_kw(topic_id)):
        if msg.empty or msg.service:
            continue
        if from_msg_id > 0 and msg.id < from_msg_id:
            break
        if msg.media_group_id:
            if msg.media_group_id in seen_groups:
                continue
            seen_groups.add(msg.media_group_id)
        mc, _ = await _media_count(client, chat_id, msg)
        if mc <= 0:
            continue
        posts += 1
        media += mc
    return posts, media


async def collect_batch_from_topic(
    client,
    src_chat_id: int,
    topic_id: int | None,
    topic_cfg: dict,
    global_cfg: dict,
    target_media_override: int | None = None,
    *,
    dry_run: bool = False,
) -> CollectResult:
    tid = _norm_topic_id(topic_id)
    pinned_id = topic_cfg.get("pinned_msg_id") or await get_pinned_message_id(
        client, src_chat_id, tid
    )

    start_msg_id = topic_cfg.get("start_msg_id")
    pin_mode = topic_cfg.get("pin_mode") or "latest"

    if start_msg_id:
        cursor_id = int(start_msg_id)
    else:
        cursor_id = topic_cfg.get("cursor_msg_id") or pinned_id

    if pin_mode == "latest" and not start_msg_id:
        fresh_pin = await get_pinned_message_id(client, src_chat_id, tid)
        if fresh_pin:
            pinned_id = fresh_pin
            if not topic_cfg.get("cursor_msg_id"):
                cursor_id = fresh_pin
    pinned_text = None
    pinned_msg = None

    if pinned_id:
        try:
            pinned_msg = await client.get_messages(src_chat_id, pinned_id)
            if pinned_msg and not pinned_msg.empty:
                pinned_text = (pinned_msg.text or pinned_msg.caption or "").strip()
                if not cursor_id:
                    cursor_id = pinned_id
        except Exception as e:
            log.warning("read pinned msg: %s", e)

    supergroup_auto = False
    if not cursor_id and tid == 0:
        cursor_id = await _find_oldest_media_cursor(client, src_chat_id)
        supergroup_auto = bool(cursor_id)

    if not cursor_id:
        rem_p, rem_m = await _count_remaining(client, src_chat_id, tid, 0)
        if not dry_run:
            update_after_scan(src_chat_id, tid, rem_p, rem_m)
        warn = "⚠️ Nhóm không có bài media (hoặc userbot chưa vào nhóm)."
        if rem_m > 0:
            warn = f"⚠️ Có {rem_m} media trong nhóm — ghim bài bắt đầu hoặc dán link tin rồi Lưu."
        return CollectResult(
            posts=[], total_media=0, total_posts=0,
            pinned_msg_id=pinned_id, next_pin_msg_id=None,
            pinned_text=pinned_text,
            params=resolve_batch_params(pinned_text, topic_cfg, global_cfg),
            remaining_posts=rem_p, remaining_media=rem_m,
            cursor_msg_id=None,
            sufficient=False,
            warn=warn,
        )

    params = resolve_batch_params(pinned_text, topic_cfg, global_cfg)
    target_media = target_media_override or params["target_media"]

    posts: list[AtomicPost] = []
    total_media = 0
    seen_groups: set[str] = set()
    last_taken_id: int | None = None
    next_pin: int | None = None

    async for msg in _iter_topic_posts_from(client, src_chat_id, tid, cursor_id):
        if msg.media_group_id and msg.media_group_id in seen_groups:
            continue
        mc, album_ids = await _media_count(client, src_chat_id, msg)
        if mc <= 0:
            continue
        if msg.media_group_id:
            seen_groups.add(msg.media_group_id)

        if total_media >= target_media:
            next_pin = msg.id
            break

        posts.append(AtomicPost(
            msg_id=msg.id,
            media_count=mc,
            is_album=len(album_ids) > 1,
            album_ids=album_ids,
        ))
        total_media += mc
        last_taken_id = msg.id

        if total_media >= target_media:
            next_pin = None
            break

    if last_taken_id and next_pin is None:
        next_pin = await _find_next_post_id(client, src_chat_id, tid, last_taken_id, seen_groups)

    sufficient = total_media >= target_media
    if sufficient:
        rem_posts, rem_media = await _count_remaining(
            client, src_chat_id, tid, next_pin or (last_taken_id or cursor_id)
        )
        scan_cursor = next_pin or (last_taken_id or cursor_id)
    else:
        rem_posts, rem_media = await _count_remaining(
            client, src_chat_id, tid, cursor_id
        )
        scan_cursor = cursor_id

    if not dry_run:
        update_after_scan(src_chat_id, tid, rem_posts, rem_media, scan_cursor, pinned_id)

    warn = check_low_stock(src_chat_id, tid, target_media if sufficient else total_media)

    if not posts:
        rem_p, rem_m = await _count_remaining(client, src_chat_id, tid, cursor_id or 0)
        if rem_m > 0 and cursor_id:
            extra = (
                f"⚠️ Có {rem_m} media từ msg {cursor_id} nhưng batch trống — "
                "ghim/dán link đúng bài bắt đầu lượt up."
            )
            warn = extra if not warn else f"{warn}\n{extra}"
        elif rem_m == 0:
            extra = "⚠️ Không thấy bài media trong nhóm (500 tin gần nhất)."
            warn = extra if not warn else f"{warn}\n{extra}"
    elif supergroup_auto and not warn:
        warn = f"ℹ️ Supergroup: tự lấy từ msg {cursor_id} (chưa ghim)."

    return CollectResult(
        posts=posts,
        total_media=total_media,
        total_posts=len(posts),
        pinned_msg_id=pinned_id,
        next_pin_msg_id=next_pin if sufficient else None,
        pinned_text=pinned_text,
        params=params,
        remaining_posts=rem_posts,
        remaining_media=rem_media,
        cursor_msg_id=cursor_id,
        sufficient=sufficient,
        warn=warn,
    )


async def _find_next_post_id(client, chat_id, topic_id, after_id, already_seen_groups):
    found_after = False
    seen = set(already_seen_groups)
    async for msg in client.get_chat_history(chat_id, limit=300, **_history_kw(topic_id)):
        if msg.empty or msg.service:
            continue
        if msg.id == after_id:
            found_after = True
            continue
        if not found_after:
            continue
        if msg.media_group_id:
            if msg.media_group_id in seen:
                continue
            seen.add(msg.media_group_id)
        mc, _ = await _media_count(client, chat_id, msg)
        if mc > 0:
            return msg.id
    return None


def posts_to_content_refs(posts: list[AtomicPost], chat_id: int) -> list[tuple[str | int, int]]:
    """Convert to (chat, msg_id) sequence items — dùng src chat trực tiếp."""
    return [(chat_id, p.msg_id) for p in posts]
