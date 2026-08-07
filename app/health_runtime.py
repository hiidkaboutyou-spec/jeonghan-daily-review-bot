from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from .config import ConfigError, Settings
from .main import check_project
from .private_runtime import PrivateReviewApplication
from .source_health import HealthTrackingXCollector, SourceHealthStore
from .telegram import inline_keyboard, main_keyboard


class HealthReviewApplication(PrivateReviewApplication):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        path = settings.state_path.with_name("private-review.sqlite3")
        self.health = SourceHealthStore(path)
        self.collector = HealthTrackingXCollector(
            settings.x_cookies, settings.sources, settings.keyword_groups, self.health
        )

    async def handle_message(self, message):
        if not self.telegram.is_admin_message(message):
            return
        text = str(message.get("text", "") or message.get("caption", "")).strip()
        command = text.partition(" ")[0].split("@", 1)[0].lower()
        if text == "🩺 سلامت منابع" or command == "/health":
            self.show_health(page=0)
            return
        await super().handle_message(message)

    async def handle_callback(self, callback):
        if not self.telegram.is_admin_callback(callback):
            return
        data = str(callback.get("data", ""))
        if data.startswith("health:page:"):
            try:
                page = int(data.split(":", 2)[2])
            except ValueError:
                page = 0
            self._answer_callback_safely(str(callback.get("id", "")))
            self.show_health(page=page, message_id=int(callback.get("message", {}).get("message_id", 0) or 0))
            return
        await super().handle_callback(callback)

    def show_health(self, *, page: int = 0, message_id: int | None = None) -> None:
        configured = [str(s.get("handle", "")).lstrip("@").lower() for s in self.settings.sources if s.get("enabled", True)]
        records = {item.source: item for item in self.health.list_all()}
        page_size = 7
        pages = max(1, (len(configured) + page_size - 1) // page_size)
        page = max(0, min(page, pages - 1))
        chunk = configured[page * page_size : (page + 1) * page_size]
        lines = [f"🩺 سلامت منابع خصوصی — صفحه {page + 1}/{pages}"]
        icons = {"healthy": "✅", "stale": "⚠️", "unhealthy": "❌", "unknown": "▫️"}
        for source in chunk:
            item = records.get(source)
            if item is None:
                lines.append(f"▫️ @{source} — هنوز دادهٔ سلامت ثبت نشده")
                continue
            status = item.status()
            success = _short_time(item.last_success, self.settings.timezone)
            lines.append(
                f"{icons[status]} @{source} — آخرین موفقیت: {success} — نتیجه: {item.recent_result_count} — خطای پیاپی: {item.consecutive_failures} — {item.last_latency_ms}ms"
            )
        nav = []
        if page > 0:
            nav.append(("◀️ قبلی", f"health:page:{page - 1}"))
        if page + 1 < pages:
            nav.append(("بعدی ▶️", f"health:page:{page + 1}"))
        markup = inline_keyboard([nav]) if nav else inline_keyboard([])
        text = "\n".join(lines)
        if message_id:
            self.telegram.edit_message_text(message_id, text, reply_markup=markup)
        else:
            self.telegram.send_message(text, reply_markup=markup or main_keyboard())


def _short_time(value: str, tz) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(tz).strftime("%m/%d %H:%M")
    except ValueError:
        return "—"


async def async_main() -> int:
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        await HealthReviewApplication(settings).run()
        return 0
    except ConfigError:
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check_project()
    return asyncio.run(async_main())
