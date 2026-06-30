"""Số bài / media theo mapping topic → kênh."""

from __future__ import annotations

import os
import re
from typing import Any

_POST_INLINE_RE = re.compile(r"^(.+?)[@|](\d+)\s*$")
_POST_COMMENT_RE = re.compile(r"(\d+)\s*b(?:ài|ai)?\b", re.I)
_MEDIA_COMMENT_RE = re.compile(r"(\d+)\s*m(?:edia)?\b", re.I)


def normalize_limits(limits: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (limits or {}).items():
        if v is None:
            continue
        n = int(v)
        if n > 0:
            out[str(k).lower().lstrip("/")] = n
    return out


normalize_post_limits = normalize_limits
normalize_media_limits = normalize_limits


def parse_map_comment(comment: str) -> tuple[int | None, int | None]:
    """# 30 media · 5 bài · vitamin → (media, posts)"""
    if not comment:
        return None, None
    media_m = _MEDIA_COMMENT_RE.search(comment)
    post_m = _POST_COMMENT_RE.search(comment)
    media = int(media_m.group(1)) if media_m else None
    posts = int(post_m.group(1)) if post_m else None
    return media, posts


def parse_map_comment_posts(comment: str) -> int | None:
    return parse_map_comment(comment)[1]


def parse_map_comment_media(comment: str) -> int | None:
    return parse_map_comment(comment)[0]


def parse_map_line(left: str, rhs: str, comment: str = "") -> tuple[str, int | None, int | None]:
    """Trả (cmd, media, posts) — chỉ một loại limit (ưu tiên số bài nếu có @N)."""
    cmd, inline_n = parse_map_rhs(rhs)
    comment_media, comment_posts = parse_map_comment(comment)
    posts = inline_n if inline_n is not None else comment_posts
    if posts and int(posts) > 0:
        return cmd, None, int(posts)
    if comment_media and int(comment_media) > 0:
        return cmd, int(comment_media), None
    return cmd, None, None


def parse_map_rhs(rhs: str) -> tuple[str, int | None]:
    """Parse phần phải dòng map: pro, pro@5, pro|10."""
    raw = (rhs or "").strip().lstrip("/")
    if not raw:
        return "", None
    m = _POST_INLINE_RE.match(raw)
    if m:
        cmd = m.group(1).strip().lstrip("/")
        return cmd, int(m.group(2))
    return raw.split()[0].lstrip("/"), None


def load_limits_from_topic_map(
    topic_title: str,
    src_id: int | None = None,
    path: str = "topic_map.txt",
) -> tuple[dict[str, int], dict[str, int]]:
    """Đọc map_media/post_limits từ topic_map.txt."""
    if not os.path.exists(path):
        return {}, {}
    title = (topic_title or "").strip().lower()
    media_out: dict[str, int] = {}
    post_out: dict[str, int] = {}
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        body, _, comment = s.partition("#")
        if "@" in body.split("=", 1)[0]:
            continue
        sep = "=" if "=" in body else (":" if ":" in body else None)
        if not sep:
            continue
        left, _, right = body.partition(sep)
        left, right = left.strip(), right.strip()
        cmd, media_n, post_n = parse_map_line(left, right, comment)
        if not cmd:
            continue
        tl = left.strip().lower()
        matched = False
        if src_id is not None:
            scoped = f"{src_id}:{title}"
            s2 = str(src_id)
            scoped_alt = f"{s2[4:]}:{title}" if s2.startswith("-100") else scoped
            if tl in (scoped, scoped_alt, f"{src_id}:{title}"):
                matched = True
        if not matched and tl == title:
            matched = True
        if matched:
            if post_n and int(post_n) > 0:
                post_out[cmd.lower()] = int(post_n)
            elif media_n and int(media_n) > 0:
                media_out[cmd.lower()] = int(media_n)
    return media_out, post_out


def apply_cmd_limit(
    entry: dict[str, Any],
    cmd: str,
    *,
    post_count: int | None = None,
    media_count: int | None = None,
) -> dict[str, Any]:
    """Gán limit cho alias — chỉ số bài HOẶC số media, không cả hai."""
    c = (cmd or "").lower().lstrip("/")
    posts = normalize_post_limits(entry.get("map_post_limits"))
    media = normalize_media_limits(entry.get("map_media_limits"))
    if post_count is not None and int(post_count) > 0:
        posts[c] = int(post_count)
        media.pop(c, None)
    elif media_count is not None and int(media_count) > 0:
        media[c] = int(media_count)
        posts.pop(c, None)
    entry["map_post_limits"] = posts
    entry["map_media_limits"] = media
    return entry


def normalize_exclusive_limits(
    map_post_limits: dict | None,
    map_media_limits: dict | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Mỗi alias chỉ giữ một loại limit — ưu tiên số bài nếu trùng."""
    posts = normalize_post_limits(map_post_limits)
    media = normalize_media_limits(map_media_limits)
    for c in list(media.keys()):
        if c in posts:
            media.pop(c, None)
    return posts, media


def limits_for_cmd(
    topic_cfg: dict[str, Any],
    cmd: str,
    *,
    topic_title: str = "",
    src_id: int | None = None,
) -> tuple[int | None, int | None]:
    """(post_lim, media_lim) — chỉ một cái khác None."""
    post_lim = post_limit_for_cmd(topic_cfg, cmd, topic_title=topic_title, src_id=src_id)
    if post_lim:
        return int(post_lim), None
    media_lim = media_limit_for_cmd(topic_cfg, cmd, topic_title=topic_title, src_id=src_id)
    if media_lim:
        return None, int(media_lim)
    return None, None


def collect_target_limits(
    topic_cfg: dict[str, Any],
    default_media: int,
) -> tuple[int | None, int | None]:
    """Giới hạn khi quét nguồn — theo bài hoặc media, không trộn."""
    post_lim = max_post_limit(topic_cfg)
    media_lim = max_media_limit(topic_cfg)
    if post_lim:
        return int(post_lim), None
    if media_lim:
        return None, int(media_lim)
    return None, int(default_media)


def media_limit_for_cmd(
    topic_cfg: dict[str, Any],
    cmd: str,
    *,
    topic_title: str = "",
    src_id: int | None = None,
) -> int | None:
    c = (cmd or "").lower().lstrip("/")
    post_limits = normalize_post_limits(topic_cfg.get("map_post_limits"))
    if c in post_limits:
        return None
    limits = normalize_media_limits(topic_cfg.get("map_media_limits"))
    if not limits and topic_title:
        limits, _ = load_limits_from_topic_map(topic_title, src_id)
    if c in limits:
        return limits[c]
    v = topic_cfg.get("target_media_override")
    if v is not None and int(v) > 0:
        return int(v)
    return None


def post_limit_for_cmd(
    topic_cfg: dict[str, Any],
    cmd: str,
    *,
    topic_title: str = "",
    src_id: int | None = None,
) -> int | None:
    """Số bài tối đa cho mapping cmd này (None = theo media mặc định)."""
    c = (cmd or "").lower().lstrip("/")
    limits = normalize_post_limits(topic_cfg.get("map_post_limits"))
    if not limits and topic_title:
        _, limits = load_limits_from_topic_map(topic_title, src_id)
    if c in limits:
        return limits[c]
    v = topic_cfg.get("target_posts_override")
    if v is not None and int(v) > 0:
        return int(v)
    return None


def max_media_limit(topic_cfg: dict[str, Any]) -> int | None:
    limits = normalize_media_limits(topic_cfg.get("map_media_limits"))
    if limits:
        return max(limits.values())
    v = topic_cfg.get("target_media_override")
    return int(v) if v is not None and int(v) > 0 else None


def max_post_limit(topic_cfg: dict[str, Any]) -> int | None:
    limits = normalize_post_limits(topic_cfg.get("map_post_limits"))
    if limits:
        return max(limits.values())
    v = topic_cfg.get("target_posts_override")
    return int(v) if v is not None and int(v) > 0 else None


def trim_posts(posts: list, limit: int | None) -> list:
    if not limit or limit <= 0:
        return list(posts)
    return list(posts[: int(limit)])


def trim_posts_by_media(posts: list, limit: int | None) -> list:
    if not limit or limit <= 0:
        return list(posts)
    out: list = []
    total = 0
    for p in posts:
        mc = int(getattr(p, "media_count", 0) or 0)
        if out and total >= int(limit):
            break
        out.append(p)
        total += mc
    return out


def format_map_comment(
    posts: int | None,
    title: str = "",
    media: int | None = None,
) -> str:
    bits: list[str] = []
    if posts and posts > 0:
        bits.append(f"{posts} bài")
    elif media and media > 0:
        bits.append(f"{media} media")
    if title:
        bits.append(title)
    return " · ".join(bits) if bits else (title or "")


def format_map_rhs(cmd: str, posts: int | None) -> str:
    c = (cmd or "").strip().lstrip("/")
    if posts and posts > 0:
        return f"{c}@{posts}"
    return c
