"""Cấu hình /all — link nguồn + kênh đích (all + up bài)."""

from __future__ import annotations

from typing import Any

from core.config_store import load_auto_config, save_auto_config
from core.link_parser import parse_source_link, parse_telegram_link, source_link_error
from core.channel_store import BRANCH_ADS, BRANCH_PLAIN, load_channels_by_ids


def apply_link_fields(task: dict[str, Any]) -> dict[str, Any]:
    """Parse link → chat/topic/msg; giữ default xếp bài."""
    out = dict(task)
    link = (out.get("start_link") or out.get("source_link") or "").strip()
    if link:
        out["start_link"] = link
        parsed = parse_source_link(link)
        err = source_link_error(parsed, link)
        if err:
            out["_link_error"] = err
        elif parsed:
            out["source_chat_id"] = parsed["src_chat_id"]
            out["source_topic_id"] = parsed.get("topic_id", 0)
            if out["source_topic_id"] is None:
                out["source_topic_id"] = 0
            if parsed.get("msg_id"):
                out["start_msg_id"] = parsed["msg_id"]
                out["cursor_msg_id"] = parsed["msg_id"]
                out["pin_mode"] = "link"
            out.pop("_link_error", None)
    out.setdefault("use_ads", False)
    out.setdefault("xep_cpa", 1)
    out.setdefault("xep_mode", "normal")
    out.setdefault("run_after_regular", False)
    out.setdefault("pin_mode", out.get("pin_mode") or "latest")
    return out


def merge_all_task(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = load_auto_config()
    cur = dict(cfg.get("all_task") or {})
    for k, v in patch.items():
        if v is not None:
            cur[k] = v
    if patch.get("source_link") is not None:
        cur["start_link"] = patch["source_link"]
    cur = apply_link_fields(cur)
    link_patch = patch.get("source_link") is not None or patch.get("start_link") is not None
    if link_patch:
        err = cur.pop("_link_error", None)
        if err:
            raise ValueError(err)
    cfg["all_task"] = cur
    save_auto_config(cfg)
    return cur


def merge_plain_task(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = load_auto_config()
    cur = dict(cfg.get("plain_task") or {
        "enabled": False,
        "selected_channel_ids": [],
        "skip_last_posts": 0,
        "included_msg_ids": None,
        "batch_signature": "",
    })
    for k, v in patch.items():
        if v is not None or k == "included_msg_ids":
            cur[k] = v
    cur["skip_last_posts"] = max(0, int(cur.get("skip_last_posts") or 0))
    if cur.get("included_msg_ids") is not None:
        cur["included_msg_ids"] = [int(x) for x in cur["included_msg_ids"]]
    cfg["plain_task"] = cur
    save_auto_config(cfg)
    return cur


def batch_signature(post_ids: list[int]) -> str:
    return ",".join(str(int(i)) for i in post_ids)


def default_plain_included(post_ids: list[int], skip_last: int = 0) -> list[int]:
    skip = max(0, int(skip_last or 0))
    if skip <= 0:
        return list(post_ids)
    if skip >= len(post_ids):
        return []
    return list(post_ids[:-skip])


def plain_skip_last(cfg: dict | None = None) -> int:
    cfg = cfg or load_auto_config()
    return max(0, int((cfg.get("plain_task") or {}).get("skip_last_posts") or 0))


def resolve_plain_posts(posts: list, skip_last: int | None = None, cfg: dict | None = None) -> list:
    """Lọc bài cho nhánh Up bài — ưu tiên tick chọn trên web."""
    cfg = cfg or load_auto_config()
    plain = cfg.get("plain_task") or {}
    if not plain.get("enabled"):
        return []
    included = plain.get("included_msg_ids")
    if included is not None:
        inc = {int(x) for x in included}
        return [p for p in posts if int(getattr(p, "msg_id", p)) in inc]
    return trim_posts_for_plain(posts, skip_last=skip_last, cfg=cfg)


def trim_posts_for_plain(posts: list, skip_last: int | None = None, cfg: dict | None = None) -> list:
    """Bỏ N bài cuối khỏi lượt up nhánh không ads (AtomicPost hoặc msg_id list)."""
    skip = plain_skip_last(cfg) if skip_last is None else max(0, int(skip_last or 0))
    if skip <= 0 or not posts:
        return list(posts)
    if skip >= len(posts):
        return []
    return list(posts[:-skip])


def destination_channel_ids(cfg: dict | None = None) -> list[int]:
    """Kênh nhận /all + nhánh up bài (dedupe)."""
    cfg = cfg or load_auto_config()
    ids: list[int] = []
    seen: set[int] = set()
    all_t = cfg.get("all_task") or {}
    plain = cfg.get("plain_task") or {}
    if all_t.get("enabled"):
        for cid in all_t.get("selected_channel_ids") or []:
            n = int(cid)
            if n not in seen:
                seen.add(n)
                ids.append(n)
    if plain.get("enabled"):
        for cid in plain.get("selected_channel_ids") or []:
            n = int(cid)
            if n not in seen:
                seen.add(n)
                ids.append(n)
    return ids


def load_destination_channels(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or load_auto_config()
    all_t = cfg.get("all_task") or {}
    plain = cfg.get("plain_task") or {}
    all_ids = [int(i) for i in all_t.get("selected_channel_ids") or []] if all_t.get("enabled") else []
    plain_ids = [int(i) for i in plain.get("selected_channel_ids") or []] if plain.get("enabled") else []
    seen: set[int] = set()
    out: list[dict] = []
    for ch in load_channels_by_ids(all_ids, BRANCH_ADS) + load_channels_by_ids(plain_ids, BRANCH_PLAIN):
        cid = int(ch["id"])
        if cid not in seen:
            seen.add(cid)
            out.append(ch)
    return out


def split_destination_channels(cfg: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Kênh tab /all (có thể xen ads) vs tab Up bài (không ads, pool riêng)."""
    cfg = cfg or load_auto_config()
    all_t = cfg.get("all_task") or {}
    plain = cfg.get("plain_task") or {}
    all_ids = [int(i) for i in all_t.get("selected_channel_ids") or []] if all_t.get("enabled") else []
    plain_ids = [int(i) for i in plain.get("selected_channel_ids") or []] if plain.get("enabled") else []
    return (
        load_channels_by_ids(all_ids, BRANCH_ADS),
        load_channels_by_ids(plain_ids, BRANCH_PLAIN),
    )


def all_source_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or load_auto_config()
    t = cfg.get("all_task") or {}
    return bool(t.get("enabled") and t.get("source_chat_id"))
