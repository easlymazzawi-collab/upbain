"""
Collect atomic posts from forum topic starting at pinned message.
Rule: bài/album không tách — lấy nguyên cả bài; ghép đủ target media (có thể vượt 1 bài).
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from core.forum_topic import iter_topic_history, norm_topic_id
from core.inventory import check_low_stock, update_after_scan
from core.map_limits import collect_target_limits
from core.pin_manager import get_pinned_message_id
from core.topic_parser import resolve_batch_params

log = logging.getLogger("source_collector")


@dataclass
class AtomicPost:
    msg_id: int
    media_count: int
    is_album: bool = False
    album_ids: list[int] = field(default_factory=list)
    is_text: bool = False


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


def _has_text(msg) -> bool:
    return bool((msg.text or msg.caption or "").strip())


async def _find_oldest_post_cursor(client, chat_id: int, *, include_text: bool, limit: int = 500) -> int | None:
    """Tin cũ nhất (media hoặc text) trong cửa sổ quét."""
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
        if mc > 0 or (include_text and _has_text(msg)):
            ids.append(msg.id)
    return min(ids) if ids else None


async def _iter_topic_posts_from(
    client, chat_id: int, topic_id: int | None, start_msg_id: int, *, include_text: bool = False,
):
    """Messages from cursor (forum topic or whole supergroup), oldest→newest."""
    seen_groups: set[str] = set()
    candidates = []

    async for msg in iter_topic_history(client, chat_id, topic_id, limit=500):
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
        if mc <= 0 and not (include_text and _has_text(msg)):
            continue
        candidates.append(msg)

    if not candidates and start_msg_id:
        try:
            msg = await client.get_messages(chat_id, start_msg_id)
            if msg and not msg.empty and not msg.service:
                mc, _ = await _media_count(client, chat_id, msg)
                if mc > 0 or (include_text and _has_text(msg)):
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


async def _count_remaining(
    client, chat_id: int, topic_id: int | None, from_msg_id: int, *, include_text: bool = False,
) -> tuple[int, int]:
    posts = 0
    media = 0
    seen_groups: set[str] = set()
    async for msg in iter_topic_history(client, chat_id, topic_id, limit=1000):
        if msg.empty or msg.service:
            continue
        if from_msg_id > 0 and msg.id < from_msg_id:
            break
        if msg.media_group_id:
            if msg.media_group_id in seen_groups:
                continue
            seen_groups.add(msg.media_group_id)
        mc, _ = await _media_count(client, chat_id, msg)
        if mc <= 0 and not (include_text and _has_text(msg)):
            continue
        posts += 1
        media += mc if mc > 0 else (1 if include_text else 0)
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
    tid = norm_topic_id(topic_id)
    all_task_mode = bool(topic_cfg.get("_all_task_mode"))
    include_text = bool(topic_cfg.get("include_text_posts", all_task_mode))

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
        cursor_id = await _find_oldest_post_cursor(client, src_chat_id, include_text=include_text)
        supergroup_auto = bool(cursor_id)

    if not cursor_id:
        rem_p, rem_m = await _count_remaining(
            client, src_chat_id, tid, 0, include_text=include_text,
        )
        if not dry_run:
            update_after_scan(src_chat_id, tid, rem_p, rem_m)
        warn = "⚠️ Nhóm trống (hoặc userbot chưa vào nhóm)."
        if rem_p > 0:
            warn = f"⚠️ Có {rem_p} bài trong nhóm — ghim hoặc dán link tin bắt đầu rồi Lưu."
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
    target_posts, target_media = collect_target_limits(
        topic_cfg,
        int(target_media_override or params["target_media"]),
    )
    count_by_posts = target_posts is not None
    max_posts = int(topic_cfg.get("max_posts") or 100)

    posts: list[AtomicPost] = []
    total_media = 0
    seen_groups: set[str] = set()
    last_taken_id: int | None = None
    next_pin: int | None = None

    async for msg in _iter_topic_posts_from(
        client, src_chat_id, tid, cursor_id, include_text=include_text,
    ):
        if msg.media_group_id and msg.media_group_id in seen_groups:
            continue
        mc, album_ids = await _media_count(client, src_chat_id, msg)
        is_text = mc <= 0 and include_text and _has_text(msg)
        if mc <= 0 and not is_text:
            continue
        if msg.media_group_id:
            seen_groups.add(msg.media_group_id)

        if all_task_mode:
            if len(posts) >= max_posts:
                next_pin = msg.id
                break
        elif count_by_posts and len(posts) >= int(target_posts):
            next_pin = msg.id
            break
        elif not count_by_posts and total_media >= int(target_media):
            next_pin = msg.id
            break

        posts.append(AtomicPost(
            msg_id=msg.id,
            media_count=mc,
            is_album=len(album_ids) > 1,
            album_ids=album_ids,
            is_text=is_text,
        ))
        total_media += mc
        last_taken_id = msg.id

        if not all_task_mode and not count_by_posts and total_media >= int(target_media):
            break

    if last_taken_id and next_pin is None and not all_task_mode:
        next_pin = await _find_next_post_id(
            client, src_chat_id, tid, last_taken_id, seen_groups, include_text=include_text,
        )

    if all_task_mode:
        sufficient = len(posts) > 0
    elif count_by_posts:
        sufficient = len(posts) >= int(target_posts)
    else:
        sufficient = total_media >= int(target_media)
    if sufficient:
        rem_posts, rem_media = await _count_remaining(
            client, src_chat_id, tid, next_pin or (last_taken_id or cursor_id),
            include_text=include_text,
        )
        scan_cursor = next_pin or (last_taken_id or cursor_id)
    else:
        rem_posts, rem_media = await _count_remaining(
            client, src_chat_id, tid, cursor_id, include_text=include_text,
        )
        scan_cursor = cursor_id

    if not dry_run:
        update_after_scan(src_chat_id, tid, rem_posts, rem_media, scan_cursor, pinned_id)

    warn = None if all_task_mode else check_low_stock(
        src_chat_id, tid,
        (int(target_posts) if count_by_posts else int(target_media)) if sufficient else total_media,
    )

    if not posts:
        rem_p, rem_m = await _count_remaining(
            client, src_chat_id, tid, cursor_id or 0, include_text=include_text,
        )
        if rem_p > 0 and cursor_id:
            extra = (
                f"⚠️ Có {rem_p} bài từ msg {cursor_id} nhưng batch trống — "
                "ghim/dán link đúng bài bắt đầu."
            )
            warn = extra if not warn else f"{warn}\n{extra}"
        elif rem_p == 0:
            extra = "⚠️ Không thấy bài trong nhóm (500 tin gần nhất)."
            warn = extra if not warn else f"{warn}\n{extra}"
    elif supergroup_auto and not warn:
        warn = f"ℹ️ Supergroup: tự lấy từ msg {cursor_id} (chưa ghim)."
    elif all_task_mode and posts and not warn:
        text_n = sum(1 for p in posts if p.is_text)
        if text_n:
            warn = f"ℹ️ /all: {len(posts)} bài ({text_n} text) — không cần đủ 30 media."

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


async def find_next_post_id(client, chat_id, topic_id, after_id, *, include_text=False):
    """Tin kế tiếp sau after_id (dùng khi trim batch theo số bài map)."""
    return await _find_next_post_id(client, chat_id, topic_id, after_id, set(), include_text=include_text)


async def _find_next_post_id(client, chat_id, topic_id, after_id, already_seen_groups, *, include_text=False):
    found_after = False
    seen = set(already_seen_groups)
    async for msg in iter_topic_history(client, chat_id, topic_id, limit=300):
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
        if mc > 0 or (include_text and _has_text(msg)):
            return msg.id
    return None


def posts_to_content_refs(posts: list[AtomicPost], chat_id: int) -> list[tuple[str | int, int]]:
    """Convert to (chat, msg_id) sequence items — dùng src chat trực tiếp."""
    return [(chat_id, p.msg_id) for p in posts]
