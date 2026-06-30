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
    cmd, inline_n = parse_map_rhs(rhs)
    comment_media, comment_posts = parse_map_comment(comment)
    posts = inline_n if inline_n is not None else comment_posts
    return cmd, comment_media, posts


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
            if media_n:
                media_out[cmd.lower()] = int(media_n)
            if post_n:
                post_out[cmd.lower()] = int(post_n)
    return media_out, post_out


def media_limit_for_cmd(
    topic_cfg: dict[str, Any],
    cmd: str,
    *,
    topic_title: str = "",
    src_id: int | None = None,
) -> int | None:
    c = (cmd or "").lower().lstrip("/")
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
    if media and media > 0:
        bits.append(f"{media} media")
    if posts and posts > 0:
        bits.append(f"{posts} bài")
    if title:
        bits.append(title)
    return " · ".join(bits) if bits else (title or "")


def format_map_rhs(cmd: str, posts: int | None) -> str:
    c = (cmd or "").strip().lstrip("/")
    if posts and posts > 0:
        return f"{c}@{posts}"
    return c
