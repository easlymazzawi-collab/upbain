"""Số bài theo mapping topic → kênh (map_post_limits)."""

from __future__ import annotations

import os
import re
from typing import Any

_POST_INLINE_RE = re.compile(r"^(.+?)[@|](\d+)\s*$")
_POST_COMMENT_RE = re.compile(r"(\d+)\s*b(?:ài|ai)?\b", re.I)


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


def parse_map_comment_posts(comment: str) -> int | None:
    """# 5 bài · vitamin → 5"""
    if not comment:
        return None
    m = _POST_COMMENT_RE.search(comment)
    return int(m.group(1)) if m else None


def parse_map_line(left: str, rhs: str, comment: str = "") -> tuple[str, int | None]:
    cmd, inline_n = parse_map_rhs(rhs)
    comment_n = parse_map_comment_posts(comment)
    n = inline_n if inline_n is not None else comment_n
    return cmd, n


def normalize_post_limits(limits: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for k, v in (limits or {}).items():
        if v is None:
            continue
        n = int(v)
        if n > 0:
            out[str(k).lower().lstrip("/")] = n
    return out


def load_limits_from_topic_map(
    topic_title: str,
    src_id: int | None = None,
    path: str = "topic_map.txt",
) -> dict[str, int]:
    """Đọc map_post_limits từ topic_map.txt (khi chưa sync config)."""
    if not os.path.exists(path):
        return {}
    title = (topic_title or "").strip().lower()
    out: dict[str, int] = {}
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
        cmd, post_n = parse_map_line(left, right, comment)
        if not cmd or not post_n:
            continue
        tl = left.strip().lower()
        matched = False
        if src_id is not None:
            scoped = f"{src_id}:{title}"
            s2 = str(src_id)
            if s2.startswith("-100"):
                scoped_alt = f"{s2[4:]}:{title}"
            else:
                scoped_alt = scoped
            if tl in (scoped, scoped_alt, f"{src_id}:{title}"):
                matched = True
        if not matched and tl == title:
            matched = True
        if matched:
            out[cmd.lower()] = int(post_n)
    return out


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
        limits = load_limits_from_topic_map(topic_title, src_id)
    if c in limits:
        return limits[c]
    v = topic_cfg.get("target_posts_override")
    if v is not None and int(v) > 0:
        return int(v)
    return None


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


def format_map_comment(posts: int | None, title: str = "") -> str:
    bits: list[str] = []
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
