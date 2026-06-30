"""Lịch chạy auto hằng ngày — chạy tất cả topic + /all task theo giờ cấu hình web."""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from core.config_store import load_auto_config
from core.runtime import append_log, get_runtime, set_status
from core.settings import system_armed

log = logging.getLogger("scheduler")

RunCycleFn = Callable[[], Awaitable[None]]


def _parse_time(s: str) -> tuple[int, int] | None:
    s = (s or "").strip()
    if not s or ":" not in s:
        return None
    parts = s.split(":", 1)
    try:
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except ValueError:
        pass
    return None


def compute_next_run_ts(times: list[str], tz_name: str, after: float | None = None) -> int:
    """Unix timestamp lần chạy tiếp theo."""
    parsed = [t for t in (_parse_time(x) for x in times) if t]
    if not parsed:
        return 0

    try:
        tz = ZoneInfo(tz_name or "Asia/Ho_Chi_Minh")
    except Exception:
        tz = ZoneInfo("Asia/Ho_Chi_Minh")

    base = datetime.fromtimestamp(after or time.time(), tz=tz)
    candidates: list[datetime] = []

    for day_offset in (0, 1):
        day = (base + timedelta(days=day_offset)).date()
        for h, m in parsed:
            dt = datetime(day.year, day.month, day.day, h, m, 0, tzinfo=tz)
            if dt.timestamp() > (after or time.time()):
                candidates.append(dt)

    if not candidates:
        return 0
    return int(min(candidates).timestamp())


def _slot_key(now: datetime, hm: tuple[int, int]) -> str:
    h, m = hm
    return f"{now.date().isoformat()}:{h:02d}:{m:02d}"


async def scheduler_loop(run_cycle: RunCycleFn) -> None:
    """Kiểm tra mỗi 20s; tại giờ đã set → chạy full cycle một lần."""
    fired: set[str] = set()
    running = False

    while True:
        try:
            cfg = load_auto_config()
            g = cfg.get("global", {})
            sch = g.get("schedule") or {}
            enabled = bool(sch.get("enabled"))
            times = sch.get("times") or []
            tz_name = sch.get("timezone") or "Asia/Ho_Chi_Minh"

            next_ts = compute_next_run_ts(times, tz_name) if enabled and times else 0
            rt = get_runtime()
            if rt.get("status") != "running":
                set_status(rt.get("status", "idle"), rt.get("current_task", ""), next_run_at=next_ts)

            if enabled and times and not running and system_armed():
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = ZoneInfo("Asia/Ho_Chi_Minh")
                now = datetime.now(tz)
                for t in times:
                    hm = _parse_time(t)
                    if not hm:
                        continue
                    h, m = hm
                    if now.hour == h and now.minute == m:
                        key = _slot_key(now, hm)
                        if key not in fired:
                            fired.add(key)
                            if len(fired) > 500:
                                fired.clear()
                            running = True
                            append_log("info", f"⏰ Lịch {h:02d}:{m:02d} — bắt đầu lượt auto")
                            try:
                                await run_cycle()
                                append_log("info", "✅ Hoàn thành lượt auto theo lịch")
                            except Exception as e:
                                log.exception("scheduled cycle: %s", e)
                                append_log("error", f"Lỗi lịch auto: {e}")
                            finally:
                                running = False
                            break
        except Exception as e:
            log.warning("scheduler tick: %s", e)

        await asyncio.sleep(20)
