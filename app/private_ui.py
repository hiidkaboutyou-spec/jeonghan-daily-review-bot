from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import Any

from .telegram import inline_keyboard


def source_page_keyboard(sources: list[dict[str, Any]], page: int, page_size: int = 6):
    """Show only enabled configured sources for 24-hour retrieval.

    Phase 1 source authority deliberately removes the old custom-source escape hatch:
    normal non-Fanfic retrieval may never fetch an arbitrary X account.
    """
    enabled = [source for source in sources if source.get("enabled", True)]
    page_size = max(1, min(int(page_size), 10))
    pages = max(1, ceil(len(enabled) / page_size))
    page = max(0, min(int(page), pages - 1))
    start = page * page_size
    chunk = enabled[start : start + page_size]
    rows = [[(f"@{source['handle']}", f"source:24:{source['handle']}")] for source in chunk]
    nav = []
    if page > 0:
        nav.append(("◀️ قبلی", f"srcpage:{page - 1}"))
    nav.append((f"{page + 1}/{pages}", "noop:page"))
    if page + 1 < pages:
        nav.append(("بعدی ▶️", f"srcpage:{page + 1}"))
    rows.append(nav)
    return inline_keyboard(rows), page, pages


def date_picker_keyboard(today: date, offset_days: int = 0):
    # offset 0 shows today and the six previous days. Negative offsets move backward.
    offset_days = min(0, int(offset_days))
    end = today + timedelta(days=offset_days)
    days = [end - timedelta(days=i) for i in range(7)]
    rows = []
    for index in range(0, len(days), 2):
        row = []
        for value in days[index : index + 2]:
            label = "امروز" if value == today else value.strftime("%m/%d")
            row.append((f"📅 {label}", f"datepick:{value:%Y%m%d}"))
        rows.append(row)
    nav = [("◀️ هفته قبل", f"datepage:{offset_days - 7}")]
    if offset_days < 0:
        nav.append(("هفته بعد ▶️", f"datepage:{min(0, offset_days + 7)}"))
    rows.append(nav)
    rows.append([("✍️ تاریخ/توضیح را تایپ می‌کنم", "noop:typed-search")])
    return inline_keyboard(rows)


def callback_data_lengths(markup: dict[str, Any]) -> list[int]:
    values = []
    for row in markup.get("inline_keyboard", []):
        for button in row:
            values.append(len(str(button.get("callback_data", "")).encode("utf-8")))
    return values
