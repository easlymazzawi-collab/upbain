"""Orchestrate auto batch: collect from source topic → xếp → forward → pin/inventory."""

import logging
from typing import Any, Callable, Awaitable

from core.channel_store import BRANCH_PLAIN
from core.branch_map import find_topic_entry, sources_key
from core.config_store import get_topic_source, load_auto_config, save_auto_config, topic_key, upsert_topic_source
from core.inventory import update_after_batch
from core.link_parser import escape_html, msg_link
from core.map_config import clear_start_link, resolve_xep_settings
from core.pin_manager import advance_topic_pin
from core.map_limits import media_limit_for_cmd, post_limit_for_cmd, trim_posts, trim_posts_by_media, limits_for_cmd
from core.source_collector import collect_batch_from_topic, find_next_post_id, posts_to_content_refs
from core.stock_watcher import mark_ready, mark_waiting, notify_wait, require_full_batch
from core.up_confirm import expire_pending_near_schedule, is_pending, offer_up_confirm, require_up_confirm

log = logging.getLogger("auto_runner")

NotifyFn = Callable[..., Awaitable[None]]


async def _notify_html(notify: NotifyFn, text: str) -> None:
    await notify(text, parse_mode="HTML")


def _topic_header(title: str, src_chat_id: int, topic_id: int) -> str:
    name = escape_html(title or f"topic {topic_id}")
    return f"📂 <b>{name}</b> · {msg_link(src_chat_id, topic_id, label='topic')}"


async def run_topic_batch(
    client,
    src_chat_id: int,
    topic_id: int,
    topic_title: str,
    *,
    notify: NotifyFn,
    build_and_forward: Callable[..., Awaitable[None]],
    find_cmds_for_topic: Callable[[str, int | None], list],
    resolve_channels_by_cmd: Callable[[str], list],
    pick_next_rr: Callable[[str, list], str],
    channels_no_ads_filter: Callable[[list, list[str]], list] | None = None,
    force_run: bool = False,
    check_only: bool = False,
    branch: str = "ads",
) -> bool:
    """
    Full auto pipeline for one mapped source topic.
    branch: ads | plain (Up bài — không xen ads, pool plain_channels.json)
    """
    cfg = load_auto_config()
    g = cfg.get("global", {})
    sk = sources_key(branch)
    tcfg = cfg.get(sk, {}).get(topic_key(src_chat_id, topic_id))
    if not tcfg and topic_title:
        _, tcfg = find_topic_entry(
            branch=branch,
            src_chat_id=src_chat_id,
            topic_id=topic_id,
            topic_title=topic_title,
        )
    tcfg = dict(tcfg or {})
    if not tcfg.get("enabled", True):
        await notify(f"⏸️ Topic '{topic_title}' tắt auto trên web.")
        return False

    tcfg.setdefault("topic_title", topic_title)
    upsert_topic_source(src_chat_id, topic_id, tcfg, branch=branch)

    branch_label = "Up bài" if branch == BRANCH_PLAIN else "Ads"

    cursor_msg = tcfg.get("start_msg_id") or tcfg.get("cursor_msg_id")
    start_bits = []
    if tcfg.get("start_link"):
        start_bits.append(f"🔗 Start: {msg_link(src_chat_id, topic_id, tcfg.get('start_msg_id'), label='link')}")
    elif cursor_msg:
        start_bits.append(f"📍 Cursor: {msg_link(src_chat_id, topic_id, cursor_msg)}")

    await _notify_html(
        notify,
        f"▶️ Bắt đầu [{branch_label}]\n{_topic_header(topic_title, src_chat_id, topic_id)}"
        + (f"\n{' · '.join(start_bits)}" if start_bits else ""),
    )

    result = await collect_batch_from_topic(client, src_chat_id, topic_id, tcfg, g)
    params = result.params
    target_media = int(params["target_media"])

    mapped_cmds = tcfg.get("mapped_cmds") or find_cmds_for_topic(topic_title, src_chat_id)
    if not mapped_cmds:
        mapped_cmds = find_cmds_for_topic(topic_title, src_chat_id)

    picked = ""
    post_lim: int | None = None
    media_lim: int | None = None
    if mapped_cmds:
        picked = pick_next_rr(topic_title, mapped_cmds) if len(mapped_cmds) > 1 else mapped_cmds[0]
        post_lim, media_lim = limits_for_cmd(
            tcfg, picked, topic_title=topic_title, src_id=src_chat_id,
        )

    tkey = topic_key(src_chat_id, topic_id)

    if result.warn and result.sufficient:
        await notify(result.warn)

    if not result.posts:
        mark_waiting(tkey, topic_title or str(topic_id), 0, target_media)
        pin_line = ""
        if result.pinned_msg_id:
            pin_line = f"\n📌 Ghim: {msg_link(src_chat_id, topic_id, result.pinned_msg_id)}"
        await _notify_html(
            notify,
            f"⚠️ {_topic_header(topic_title, src_chat_id, topic_id)}\n"
            f"Không lấy được bài từ nguồn — chờ bổ sung.{pin_line}",
        )
        return False

    posts = list(result.posts)
    n_posts = len(posts)
    n_media = result.total_media

    if post_lim:
        sufficient = n_posts >= post_lim
        need_label = f"{post_lim} bài"
        need_count = post_lim
    elif media_lim:
        sufficient = n_media >= media_lim
        need_label = f"{media_lim} media"
        need_count = media_lim
    else:
        sufficient = result.sufficient
        need_label = f"{target_media} media"
        need_count = target_media

    must_full = require_full_batch()
    if must_full and not sufficient:
        have = n_posts if post_lim else (n_media if media_lim else n_media)
        mark_waiting(tkey, topic_title or str(topic_id), have, need_count)
        await notify_wait(
            notify,
            tkey,
            f"⏸ {_topic_header(topic_title, src_chat_id, topic_id)}\n"
            f"Chưa đủ: <b>{have}/{need_count}</b> {need_label.split()[-1]}\n"
            f"Thiếu {need_count - have} — <b>chờ bổ sung</b>, không up kênh\n"
            f"📉 Kho từ cursor: {result.remaining_media} media / {result.remaining_posts} bài\n"
            f"🔄 Tool tự thử lại mỗi {g.get('stock_poll_interval_sec') or 300}s",
        )
        return False

    if require_up_confirm() and not force_run:
        offered = await offer_up_confirm(
            notify,
            tkey,
            title=topic_title or str(topic_id),
            src_chat_id=src_chat_id,
            topic_id=topic_id,
            kind="topic",
            have_media=n_media if not post_lim else n_posts,
            need_media=need_count,
            header_html=_topic_header(topic_title, src_chat_id, topic_id),
            branch=branch,
        )
        if offered:
            return False

    mark_ready(tkey)
    if result.warn:
        await notify(result.warn)

    if not mapped_cmds:
        await _notify_html(
            notify,
            f"📥 {_topic_header(topic_title, src_chat_id, topic_id)}\n"
            f"Lấy {n_posts} bài / {n_media} media\n"
            f"⚠️ Chưa map kênh — cấu hình trên web hoặc topic_map.txt",
        )
        return False

    if post_lim:
        posts = trim_posts(posts, post_lim)
    elif media_lim:
        posts = trim_posts_by_media(posts, media_lim)
    n_posts = len(posts)
    n_media = sum(p.media_count for p in posts)
    content_refs = posts_to_content_refs(posts, src_chat_id)

    no_ads_cmds = set(x.lower() for x in tcfg.get("channels_no_ads", []))
    channels = resolve_channels_by_cmd(picked)
    if branch == BRANCH_PLAIN:
        use_ads = False
    elif channels_no_ads_filter and picked.lower() in no_ads_cmds:
        use_ads = False
    else:
        use_ads = picked.lower() not in no_ads_cmds

    xep = resolve_xep_settings(tcfg, g, picked)
    if not use_ads:
        xep = {**xep, "use_ads": False}

    media_per_round = params.get("media_per_round") or n_media
    rounds_note = ""
    if len(mapped_cmds) > 1 and media_per_round:
        rounds_note = f" | ~{media_per_round} media/lượt RR"

    pin_line = ""
    if result.pinned_msg_id:
        pin_line = f"\n📌 Ghim: {msg_link(src_chat_id, topic_id, result.pinned_msg_id)}"
    next_line = ""
    if result.next_pin_msg_id:
        next_line = f"\n⏭ Ghim tiếp: {msg_link(src_chat_id, topic_id, result.next_pin_msg_id)}"

    target_note = (
        f"{post_lim} bài" if post_lim
        else (f"{media_lim} media" if media_lim else f"{params['target_media']} media")
    )
    await _notify_html(
        notify,
        f"🎯 {_topic_header(topic_title, src_chat_id, topic_id)}\n"
        f"→ /{escape_html(picked)}{rounds_note}\n"
        f"📥 {n_posts} bài / {n_media} media (map: {target_note})\n"
        f"📦 Ads: {params['target_ads'] if xep['use_ads'] else 0} | /done{xep['default_cpa']} mode={xep['mode']}\n"
        f"📉 Kho còn: ~{result.remaining_media} media / {result.remaining_posts} bài"
        f"{pin_line}{next_line}",
    )

    slot_data: dict[str, Any] = {
        "content_msgs": [mid for _, mid in content_refs],
        "content_chat": src_chat_id,
        "total_media_count": n_media,
        "topic_title": topic_title,
        "topic_src_id": src_chat_id,
        "topic_id": topic_id,
        "use_ads": xep["use_ads"],
        "target_ads": params["target_ads"],
        "default_cpa": xep["default_cpa"],
        "mode": xep["mode"],
        "_from_source": True,
        "_collect_result": result,
        "_atomic_posts": posts,
        "_branch": branch,
    }

    await build_and_forward(slot_data, channels, picked)

    if tcfg.get("start_msg_id") and tcfg.get("pin_mode") == "link":
        clear_start_link(src_chat_id, topic_id)

    next_pin_id = result.next_pin_msg_id
    if post_lim and posts and len(posts) < len(result.posts):
        include_text = bool(tcfg.get("include_text_posts"))
        found = await find_next_post_id(
            client, src_chat_id, topic_id, posts[-1].msg_id, include_text=include_text,
        )
        if found:
            next_pin_id = found

    if next_pin_id:
        pin_err: Exception | None = None
        try:
            await advance_topic_pin(
                client, src_chat_id, topic_id,
                result.pinned_msg_id, next_pin_id,
            )
        except Exception as e:
            pin_err = e
            log.warning("advance pin: %s", e)
        upsert_topic_source(src_chat_id, topic_id, {
            "cursor_msg_id": next_pin_id,
            "pinned_msg_id": next_pin_id,
        }, branch=branch)
        if pin_err:
            await notify(
                f"⚠️ Không ghim được trên Telegram: {pin_err}\n"
                f"📍 Đã lưu cursor msg {next_pin_id} — lần sau vẫn lấy đúng bài tiếp theo.\n"
                f"Kiểm tra: Pin bot token + bot/userbot là admin nhóm nguồn (quyền ghim)."
            )

    update_after_batch(
        src_chat_id, topic_id,
        n_posts, n_media,
        next_pin_id, result.pinned_msg_id,
    )

    done_lines = [f"✅ Xong · /{escape_html(picked)}"]
    if post_lim:
        done_lines[0] += f" ({n_posts} bài)"
    if next_pin_id:
        done_lines.append(f"⏭ {msg_link(src_chat_id, topic_id, next_pin_id, label='ghim tiếp')}")
    await _notify_html(notify, f"{_topic_header(topic_title, src_chat_id, topic_id)}\n" + "\n".join(done_lines))
    return True


async def run_all_task(
    client,
    *,
    notify: Callable[[str], Awaitable[None]],
    forward_sequence_fn: Callable[..., Awaitable[Any]],
    build_and_forward_all: Callable[..., Awaitable[None]] | None = None,
    force_run: bool = False,
    check_only: bool = False,
) -> bool:
    cfg = load_auto_config()
    task = cfg.get("all_task", {})
    plain = cfg.get("plain_task") or {}
    if not task.get("enabled") and not plain.get("enabled"):
        return False
    src_chat = task.get("source_chat_id")
    src_topic = task.get("source_topic_id")
    if src_topic is None:
        src_topic = 0
    if not src_chat:
        await notify("⚠️ /all: chưa có link nguồn — dán link tab /all rồi Lưu.")
        return False

    from core.all_config import load_destination_channels, plain_skip_last, resolve_plain_posts, split_destination_channels

    all_chs, plain_chs = split_destination_channels(cfg)
    channels = load_destination_channels(cfg)

    if not channels:
        await notify("⚠️ /all: chưa chọn kênh đích trên web (tab /all hoặc Up bài).")
        return False

    base_tcfg = get_topic_source(src_chat, src_topic) or {"enabled": True}
    tcfg = {
        **base_tcfg,
        "start_msg_id": task.get("start_msg_id") or base_tcfg.get("start_msg_id"),
        "cursor_msg_id": task.get("cursor_msg_id") or base_tcfg.get("cursor_msg_id"),
        "pin_mode": task.get("pin_mode") or base_tcfg.get("pin_mode") or "latest",
        "start_link": task.get("start_link") or base_tcfg.get("start_link"),
        "target_media_override": task.get("target_media_override") or base_tcfg.get("target_media_override"),
        "default_cpa": task.get("xep_cpa") or base_tcfg.get("default_cpa"),
        "default_mode": task.get("xep_mode") or base_tcfg.get("default_mode"),
        "use_ads": task.get("use_ads", False),
        "_all_task_mode": True,
        "include_text_posts": task.get("include_text_posts", True),
    }

    result = await collect_batch_from_topic(client, src_chat, src_topic, tcfg, cfg.get("global", {}))
    g = cfg.get("global", {})
    target_media = int(result.params.get("target_media") or 30)
    tkey = topic_key(src_chat, src_topic)

    if not result.posts:
        return False

    all_must_full = bool(task.get("require_full_batch", False)) and require_full_batch()
    if all_must_full and not result.sufficient:
        mark_waiting(tkey, task.get("source_title") or "/all", result.total_media, target_media)
        await notify_wait(
            notify,
            tkey,
            f"⏸ <b>/all</b> · {msg_link(src_chat, src_topic, label='topic')}\n"
            f"Chưa đủ: <b>{result.total_media}/{target_media}</b> media — chờ bổ sung",
        )
        return False

    if task.get("require_up_confirm", False) and require_up_confirm() and not force_run:
        offered = await offer_up_confirm(
            notify,
            tkey,
            title=task.get("source_title") or "/all",
            src_chat_id=src_chat,
            topic_id=src_topic,
            kind="all_task",
            have_media=result.total_media,
            need_media=target_media,
            header_html=f"<b>/all</b> · {msg_link(src_chat, src_topic, label='topic')}",
        )
        if offered:
            return False

    mark_ready(tkey)

    xep = resolve_xep_settings(tcfg, g, "/all")
    use_ads = bool(task.get("use_ads", False))
    plain_cfg = cfg.get("plain_task") or {}
    skip_n = plain_skip_last(cfg)
    plain_posts = resolve_plain_posts(result.posts, cfg=cfg) if plain_chs else []
    plain_media = sum(p.media_count for p in plain_posts)

    plain_line = ""
    if plain_chs:
        if skip_n > 0 and plain_cfg.get("included_msg_ids") is None:
            plain_line = f"\nUp bài: {len(plain_posts)}/{result.total_posts} bài ({plain_media} media) — bỏ {skip_n} bài cuối"
        elif plain_cfg.get("included_msg_ids") is not None:
            plain_line = f"\nUp bài: {len(plain_posts)}/{result.total_posts} bài ({plain_media} media) — tick chọn trên web"
        else:
            plain_line = f"\nUp bài: {len(plain_posts)} bài ({plain_media} media)"

    await _notify_html(
        notify,
        f"📦 <b>/all</b> · {msg_link(src_chat, src_topic, label='topic')}\n"
        f"{result.total_posts} bài / {result.total_media} media → "
        f"{len(all_chs)} kênh /all + {len(plain_chs)} up bài"
        f"{plain_line}\n"
        f"Xếp: /done{xep['default_cpa']} mode={task.get('xep_mode', 'normal')} "
        f"ads={'có' if use_ads and all_chs else 'không'}",
    )

    if build_and_forward_all:
        slot_data: dict[str, Any] = {
            "content_msgs": [p.msg_id for p in result.posts],
            "plain_content_msgs": [p.msg_id for p in plain_posts],
            "plain_total_media_count": plain_media,
            "content_chat": src_chat,
            "total_media_count": result.total_media,
            "topic_title": task.get("source_title") or "/all",
            "topic_src_id": src_chat,
            "topic_id": src_topic,
            "use_ads": use_ads,
            "target_ads": result.params.get("target_ads", 0),
            "default_cpa": task.get("xep_cpa") or xep["default_cpa"],
            "mode": task.get("xep_mode") or xep["mode"],
            "_from_source": True,
            "_collect_result": result,
            "_atomic_posts": result.posts,
        }
        await build_and_forward_all(slot_data, channels)
    else:
        all_ids = {int(c["id"]) for c in all_chs}
        plain_ids = {int(c["id"]) for c in plain_chs}
        full_seq = [(src_chat, p.msg_id) for p in result.posts]
        plain_seq = [(src_chat, p.msg_id) for p in plain_posts]
        for ch in channels:
            cid = int(ch["id"])
            if cid in plain_ids and cid not in all_ids:
                await forward_sequence_fn(ch["id"], plain_seq)
            else:
                await forward_sequence_fn(ch["id"], full_seq)

    if task.get("start_msg_id") and task.get("pin_mode") == "link":
        cfg2 = load_auto_config()
        cfg2.setdefault("all_task", {})["start_msg_id"] = None
        cfg2["all_task"]["start_link"] = ""
        cfg2["all_task"]["pin_mode"] = "latest"
        save_auto_config(cfg2)

    if result.next_pin_msg_id:
        pin_err: Exception | None = None
        try:
            await advance_topic_pin(
                client, src_chat, src_topic,
                result.pinned_msg_id, result.next_pin_msg_id,
            )
        except Exception as e:
            pin_err = e
            log.warning("/all advance pin: %s", e)
        cfg2 = load_auto_config()
        at = cfg2.setdefault("all_task", {})
        at["cursor_msg_id"] = result.next_pin_msg_id
        at["pinned_msg_id"] = result.next_pin_msg_id
        save_auto_config(cfg2)
        if pin_err:
            await notify(
                f"⚠️ /all: không ghim Telegram — {pin_err}\n"
                f"📍 Đã lưu cursor msg {result.next_pin_msg_id} trong config."
            )

    return True


async def preview_topic_batch(
    client,
    src_chat_id: int,
    topic_id: int,
    topic_cfg: dict | None = None,
) -> dict[str, Any]:
    """Xem trước lượt xếp — không forward."""
    cfg = load_auto_config()
    g = cfg.get("global", {})
    tcfg = topic_cfg or get_topic_source(src_chat_id, topic_id) or {}
    result = await collect_batch_from_topic(client, src_chat_id, topic_id, tcfg, g)
    xep = resolve_xep_settings(tcfg, g, (tcfg.get("mapped_cmds") or ["?"])[0])
    return {
        "posts": result.total_posts,
        "media": result.total_media,
        "target_media": result.params.get("target_media"),
        "target_ads": result.params.get("target_ads"),
        "cpa": xep["default_cpa"],
        "mode": xep["mode"],
        "use_ads": xep["use_ads"],
        "pinned_msg_id": result.pinned_msg_id,
        "cursor_start": tcfg.get("start_msg_id") or result.pinned_msg_id,
        "next_pin": result.next_pin_msg_id,
        "remaining_media": result.remaining_media,
        "remaining_posts": result.remaining_posts,
        "sufficient": result.sufficient,
        "cursor_msg_id": result.cursor_msg_id,
        "warn": result.warn,
    }
