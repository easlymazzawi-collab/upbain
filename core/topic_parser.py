"""Parse target media/ads from pinned message text or config overrides."""

import re
from typing import Any

_MEDIA_RE = re.compile(r"(\d+)\s*media", re.I)
_ADS_RE = re.compile(r"(\d+)\s*ads?", re.I)
_BAI_RE = re.compile(r"(\d+)\s*bài", re.I)
_CPA_RE = re.compile(r"(\d+)\s*content\s*/\s*ads?", re.I)
_DONE_RE = re.compile(r"/done(\d+)", re.I)


def parse_pinned_config(text: str | None) -> dict[str, int | None]:
    out: dict[str, int | None] = {
        "target_media": None,
        "target_ads": None,
        "target_posts": None,
        "default_cpa": None,
    }
    if not text:
        return out
    m = _MEDIA_RE.search(text)
    if m:
        out["target_media"] = int(m.group(1))
    m = _ADS_RE.search(text)
    if m:
        out["target_ads"] = int(m.group(1))
    m = _BAI_RE.search(text)
    if m:
        out["target_posts"] = int(m.group(1))
    m = _CPA_RE.search(text)
    if m:
        out["default_cpa"] = int(m.group(1))
    m = _DONE_RE.search(text)
    if m:
        out["default_cpa"] = int(m.group(1))
    return out


def resolve_batch_params(
    pinned_text: str | None,
    topic_cfg: dict[str, Any],
    global_cfg: dict[str, Any],
) -> dict[str, Any]:
    parsed = parse_pinned_config(pinned_text)
    target_media = topic_cfg.get("target_media_override") or parsed.get("target_media") or 30
    target_ads = topic_cfg.get("target_ads_override") or parsed.get("target_ads") or 2
    default_cpa = (
        topic_cfg.get("default_cpa")
        or parsed.get("default_cpa")
        or global_cfg.get("xepbai_off_default_cpa")
        or 1
    )
    target_posts = topic_cfg.get("target_posts_override") or parsed.get("target_posts")
    media_per_round = topic_cfg.get("media_per_round") or target_media
    return {
        "target_media": int(target_media),
        "target_ads": int(target_ads),
        "target_posts": int(target_posts) if target_posts else None,
        "default_cpa": int(default_cpa),
        "media_per_round": int(media_per_round),
        "mode": topic_cfg.get("default_mode") or global_cfg.get("xepbai_off_default_mode") or "normal",
    }
