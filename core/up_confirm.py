"""Xác nhận /upngay trước khi up — chờ user, hủy gần giờ lịch chung."""

import logging
import time
from typing import Awaitable, Callable

from core.config_store import load_auto_config
from core.runtime import append_log, clear_all_pending_up, get_pending_up, get_runtime, set_pending_up
from core.scheduler import compute_next_run_ts

log = logging.getLogger("up_confirm")

NotifyFn = Callable[..., Awaitable[None]]
_last_offer_notify: dict[str, int] = {}
_OFFER_COOLDOWN = 3600  # 1 lần/giờ nếu vẫn pending


def require_up_confirm() -> bool:
    g = load_auto_config().get("global", {})
    if not g.get("require_full_batch", True):
        return False
    return g.get("require_up_confirm", True) is not False


def confirm_cancel_before_sec() -> int:
    v = load_auto_config().get("global", {}).get("confirm_cancel_before_sec") or 900
    return max(60, int(v))


def next_schedule_ts() -> int:
    cfg = load_auto_config()
    sch = cfg.get("global", {}).get("schedule") or {}
    if not sch.get("enabled"):
        return int(get_runtime().get("next_run_at") or 0)
    return compute_next_run_ts(
        sch.get("times") or [],
        sch.get("timezone") or "Asia/Ho_Chi_Minh",
    )


def is_pending(key: str) -> bool:
    return key in get_pending_up()


async def offer_up_confirm(
    notify: NotifyFn,
    key: str,
    *,
    title: str,
    src_chat_id: int,
    topic_id: int,
    kind: str,
    have_media: int,
    need_media: int,
    header_html: str,
    force: bool = False,
    branch: str = "ads",
) -> bool:
    """Đủ bài → chờ /upngay, không up ngay. Trả True nếu đã offer (chặn run)."""
    if not require_up_confirm():
        return False

    existing = get_pending_up().get(key)
    if existing and not force:
        now = int(time.time())
        if now - _last_offer_notify.get(key, 0) < _OFFER_COOLDOWN:
            return True
    elif existing:
        return True

    next_ts = next_schedule_ts()
    set_pending_up(
        key,
        title=title,
        src_chat_id=src_chat_id,
        topic_id=topic_id,
        kind=kind,
        have_media=have_media,
        need_media=need_media,
        next_schedule_at=next_ts,
        branch=branch,
    )

    sched_note = ""
    if next_ts:
        from datetime import datetime
        sched_note = f"\n⏰ Gần giờ lịch chung → tự hủy chờ /upngay (chạy theo lịch)"

    text = (
        f"✅ {header_html}\n"
        f"Đủ bài: <b>{have_media}/{need_media}</b> media\n"
        f"Gõ <b>/upngay</b> hoặc <b>/upngay {title}</b> để up ngay\n"
        f"Không trả lời → im lặng, không up{sched_note}"
    )
    _last_offer_notify[key] = int(time.time())
    await notify(text, parse_mode="HTML")
    append_log("info", f"Chờ /upngay: {title} ({have_media}/{need_media} media)")
    return True


def expire_pending_near_schedule(*, silent: bool = True) -> int:
    """Gần giờ lịch chung → hủy pending /upngay."""
    pending = get_pending_up()
    if not pending:
        return 0

    next_ts = next_schedule_ts()
    if not next_ts:
        return 0

    now = time.time()
    margin = confirm_cancel_before_sec()
    if now < next_ts - margin:
        return 0

    n = clear_all_pending_up()
    if n and not silent:
        append_log("info", f"⏰ Hủy {n} task chờ /upngay — sắp chạy lịch chung")
    elif n:
        append_log("info", f"⏰ Hủy {n} task /upngay (im lặng) — chạy theo lịch chung")
    for k in list(_last_offer_notify.keys()):
        if k not in get_pending_up():
            _last_offer_notify.pop(k, None)
    return n
