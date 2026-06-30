"""Ngày theo múi giờ Việt Nam."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def today_vn() -> date:
    return datetime.now(VN_TZ).date()


def date_vn_str(d: date | None = None) -> str:
    d = d or today_vn()
    return d.isoformat()


def topic_label_vn(d: date | None = None) -> str:
    d = d or today_vn()
    return d.strftime("%d-%m-%Y")


def parse_day_label(text: str) -> date | None:
    text = (text or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
