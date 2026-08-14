"""Remove the last legacy complete-window truncation under configured-source policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .configured_source_runtime import _configured_handle, _configured_updates
from .main import Application
from .telegram import main_keyboard


async def _complete_configured_source24(self: Application, value: str) -> None:
    if value == "custom":
        self.show_sources()
        return
    handle = _configured_handle(self, value)
    if not handle:
        self.telegram.send_message(
            "این حساب در لیست منابع تنظیم‌شده نیست؛ دریافت فقط از منابع تنظیم‌شده انجام می‌شود.",
            reply_markup=main_keyboard(),
        )
        return
    self.telegram.send_message(
        f"🗂 دارم ۲۴ ساعت کامل @{handle} را از قدیمی به جدید می‌گیرم…",
        reply_markup=main_keyboard(),
    )
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    updates = await self.collector.collect_source(handle, start, end)
    updates = _configured_updates(
        self,
        item for item in updates if start <= item.created_at < end,
    )
    if not updates:
        self.telegram.send_message(
            f"برای @{handle} در ۲۴ ساعت گذشته چیزی پیدا نشد.",
            reply_markup=main_keyboard(),
        )
        return
    # A complete source window is delivered in full. The completeness collector
    # raises instead of returning a silently truncated 1000-item timeline.
    await self.deliver_updates(updates, force=True)


Application.run_source24 = _complete_configured_source24
