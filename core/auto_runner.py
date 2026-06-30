"""Orchestrate auto batch: collect from source topic → xếp → forward → pin/inventory."""

import logging
from typing import Any, Callable, Awaitable

from core.config_store import get_topic_source, load_auto_config, topic_key, upsert_topic_source
from core.inventory import update_after_batch
from core.pin_manager import advance_topic_pin
from core.settings import CHANNELS_FILE
from core.source_collector import collect_batch_from_topic, posts_to_content_refs

log = logging.getLogger("auto_runner")


async def run_topic_batch(
    client,
    src_chat_id: int,
    topic_id: int,
    topic_title: str,
    *,
    notify: Callable[[str], Awaitable[None]],
    build_and_forward: Callable[..., Awaitable[None]],
    find_cmds_for_topic: Callable[[str, int | None], list],
    resolve_channels_by_cmd: Callable[[str], list],
    pick_next_rr: Callable[[str, list], str],
    channels_no_ads_filter: Callable[[list, list[str]], list] | None = None,
) -> bool:
    """
    Full auto pipeline for one mapped source topic.
    build_and_forward(slot_like_dict) — injected from main tool.
    """
    cfg = load_auto_config()
    g = cfg.get("global", {})
    tcfg = get_topic_source(src_chat_id, topic_id) or {}
    if not tcfg.get("enabled", True):
        await notify(f"⏸️ Topic '{topic_title}' tắt auto trên web.")
        return False

    tcfg.setdefault("topic_title", topic_title)
    upsert_topic_source(src_chat_id, topic_id, tcfg)

    result = await collect_batch_from_topic(client, src_chat_id, topic_id, tcfg, g)
    if result.warn:
        await notify(result.warn)
    if not result.posts:
        await notify(f"⚠️ Topic '{topic_title}': không lấy được bài từ nguồn.")
        return False

    params = result.params
    content_refs = posts_to_content_refs(result.posts, src_chat_id)
    n_posts = result.total_posts
    n_media = result.total_media

    mapped_cmds = tcfg.get("mapped_cmds") or find_cmds_for_topic(topic_title, src_chat_id)
    if not mapped_cmds:
        mapped_cmds = find_cmds_for_topic(topic_title, src_chat_id)
    if not mapped_cmds:
        await notify(
            f"📥 Lấy {n_posts} bài / {n_media} media từ topic '{topic_title}'\n"
            f"⚠️ Chưa map kênh — cấu hình trên web hoặc topic_map.txt"
        )
        return False

    no_ads_cmds = set(x.lower() for x in tcfg.get("channels_no_ads", []))
    picked = pick_next_rr(topic_title, mapped_cmds) if len(mapped_cmds) > 1 else mapped_cmds[0]
    channels = resolve_channels_by_cmd(picked)
    if channels_no_ads_filter and picked.lower() in no_ads_cmds:
        use_ads = False
    else:
        use_ads = picked.lower() not in no_ads_cmds

    media_per_round = params.get("media_per_round") or n_media
    rounds_note = ""
    if len(mapped_cmds) > 1 and media_per_round:
        rounds_note = f" | ~{media_per_round} media/lượt RR"

    await notify(
        f"🎯 Auto topic '{topic_title}' → /{picked}{rounds_note}\n"
        f"📥 Nguồn: {n_posts} bài / {n_media} media (target {params['target_media']})\n"
        f"📦 Ads: {params['target_ads'] if use_ads else 0} | CPA /done{params['default_cpa']}\n"
        f"📉 Kho còn: ~{result.remaining_media} media / {result.remaining_posts} bài"
    )

    slot_data: dict[str, Any] = {
        "content_msgs": [mid for _, mid in content_refs],
        "content_chat": src_chat_id,
        "total_media_count": n_media,
        "topic_title": topic_title,
        "topic_src_id": src_chat_id,
        "topic_id": topic_id,
        "use_ads": use_ads,
        "target_ads": params["target_ads"],
        "default_cpa": params["default_cpa"],
        "mode": params.get("mode", "normal"),
        "_from_source": True,
        "_collect_result": result,
    }

    await build_and_forward(slot_data, channels, picked)

    if result.next_pin_msg_id:
        try:
            await advance_topic_pin(
                client, src_chat_id, topic_id,
                result.pinned_msg_id, result.next_pin_msg_id,
            )
            upsert_topic_source(src_chat_id, topic_id, {
                "cursor_msg_id": result.next_pin_msg_id,
                "pinned_msg_id": result.next_pin_msg_id,
            })
        except Exception as e:
            log.warning("advance pin: %s", e)
            await notify(f"⚠️ Không ghim bài tiếp: {e}")

    update_after_batch(
        src_chat_id, topic_id,
        n_posts, n_media,
        result.next_pin_msg_id, result.pinned_msg_id,
    )
    return True


async def run_all_task(
    client,
    *,
    notify: Callable[[str], Awaitable[None]],
    forward_sequence_fn: Callable[..., Awaitable[Any]],
) -> bool:
    cfg = load_auto_config()
    task = cfg.get("all_task", {})
    if not task.get("enabled"):
        return False
    src_chat = task.get("source_chat_id")
    src_topic = task.get("source_topic_id")
    ch_ids = task.get("selected_channel_ids") or []
    if not src_chat or not src_topic or not ch_ids:
        await notify("⚠️ /all task: chưa cấu hình nguồn hoặc kênh trên web.")
        return False

    import json
    import os
    path = CHANNELS_FILE
    channels = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            all_ch = json.load(f)
        idset = {str(i) for i in ch_ids}
        channels = [c for c in all_ch if str(c.get("id")) in idset]

    tcfg = get_topic_source(src_chat, src_topic) or {"enabled": True}
    result = await collect_batch_from_topic(client, src_chat, src_topic, tcfg, cfg.get("global", {}))
    if not result.posts:
        await notify("⚠️ /all task: không có bài trong topic nguồn.")
        return False

    seq = [(src_chat, p.msg_id) for p in result.posts]
    await notify(
        f"📦 ALL TASK (cuối cùng): {result.total_posts} bài / {result.total_media} media "
        f"→ {len(channels)} kênh"
    )
    for ch in channels:
        await forward_sequence_fn(ch["id"], seq)

    if result.next_pin_msg_id:
        await advance_topic_pin(client, src_chat, src_topic, result.pinned_msg_id, result.next_pin_msg_id)

    return True
