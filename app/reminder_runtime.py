from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, time, timedelta, timezone

from .config import ConfigError, Settings
from .health_runtime import HealthReviewApplication
from .main import check_project
from .reminder_queue import ReminderStore
from .telegram import draft_keyboard, inline_keyboard, main_keyboard


class ReminderReviewApplication(HealthReviewApplication):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.reminders = ReminderStore(settings.state_path.with_name("private-review.sqlite3"))

    async def run(self) -> None:
        await super().run()
        await self.process_due_reminders()

    async def handle_message(self, message):
        if not self.telegram.is_admin_message(message):
            return
        text = str(message.get("text", "") or message.get("caption", "")).strip()
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if text == "⏰ یادآورها" or command == "/reminders":
            self.show_reminders()
            return
        if command == "/remind":
            self.create_typed_reminder(argument)
            return
        await super().handle_message(message)

    async def handle_callback(self, callback):
        if not self.telegram.is_admin_callback(callback):
            return
        data = str(callback.get("data", ""))
        callback_id = str(callback.get("id", ""))
        if data.startswith("remind:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                preset, draft_id = parts[1], parts[2]
                self._answer_callback_safely(callback_id)
                self.create_preset_reminder(draft_id, preset)
                return
        if data.startswith("remcancel:"):
            self._answer_callback_safely(callback_id)
            self.reminders.cancel(data.split(":", 1)[1])
            self.show_reminders()
            return
        await super().handle_callback(callback)

    def create_preset_reminder(self, draft_id: str, preset: str) -> None:
        if self.state.get_draft(draft_id) is None:
            self.telegram.send_message("این پیش‌نویس پیدا نشد.", reply_markup=main_keyboard())
            return
        now = datetime.now(self.settings.timezone)
        if preset == "1h":
            due = now + timedelta(hours=1)
            label = "یک ساعت بعد"
        elif preset == "tonight":
            due = datetime.combine(now.date(), time(21, 0), self.settings.timezone)
            if due <= now:
                due += timedelta(days=1)
            label = "امشب"
        elif preset == "tomorrow":
            due = datetime.combine(now.date() + timedelta(days=1), time(9, 0), self.settings.timezone)
            label = "فردا صبح"
        elif preset == "pin":
            due = None
            label = "نگه‌دار برای بعد"
        else:
            return
        self.reminders.add(draft_id, due, label=label)
        self.telegram.send_message(
            f"⏰ ذخیره شد: {label}" if due else "📌 این پیش‌نویس برای بعد نگه داشته شد.",
            reply_markup=main_keyboard(),
        )

    def create_typed_reminder(self, argument: str) -> None:
        # /remind DRAFT_ID YYYY-MM-DD HH:MM in configured local timezone.
        parts = argument.split()
        if len(parts) != 3:
            self.telegram.send_message("فرمت: /remind DRAFT_ID YYYY-MM-DD HH:MM", reply_markup=main_keyboard())
            return
        draft_id, date_value, clock = parts
        if self.state.get_draft(draft_id) is None:
            self.telegram.send_message("این draft id پیدا نشد.", reply_markup=main_keyboard())
            return
        try:
            local_due = datetime.strptime(f"{date_value} {clock}", "%Y-%m-%d %H:%M").replace(tzinfo=self.settings.timezone)
        except ValueError:
            self.telegram.send_message("تاریخ/ساعت معتبر نیست.", reply_markup=main_keyboard())
            return
        if local_due <= datetime.now(self.settings.timezone):
            self.telegram.send_message("زمان reminder باید در آینده باشد.", reply_markup=main_keyboard())
            return
        self.reminders.add(draft_id, local_due, label="زمان انتخابی")
        self.telegram.send_message(f"⏰ reminder برای {local_due:%Y-%m-%d %H:%M} ذخیره شد.", reply_markup=main_keyboard())

    async def process_due_reminders(self) -> None:
        now = datetime.now(timezone.utc)
        for job in self.reminders.due(now, limit=20):
            draft = self.state.get_draft(job.draft_id)
            if draft is None:
                self.reminders.cancel(job.id)
                continue
            # Mark sent only AFTER Telegram confirms delivery. Failure leaves it pending.
            self.telegram.send_message(
                "⏰ یادآوری خصوصی\n\n" + draft.caption,
                reply_markup=draft_keyboard(draft.id),
            )
            self.reminders.mark_sent(job.id, now)

    def show_reminders(self) -> None:
        jobs = self.reminders.list_active(limit=30)
        if not jobs:
            self.telegram.send_message("⏰ reminder فعالی نداری.", reply_markup=main_keyboard())
            return
        lines = ["⏰ یادآورهای خصوصی:"]
        rows = []
        for job in jobs:
            if job.due_at:
                try:
                    due = datetime.fromisoformat(job.due_at).astimezone(self.settings.timezone).strftime("%m/%d %H:%M")
                except ValueError:
                    due = "?"
            else:
                due = "📌 نگه‌داشته‌شده"
            lines.append(f"• {job.label or 'یادآوری'} — {due} — draft {job.draft_id[:8]}")
            rows.append([("لغو " + job.id[:6], f"remcancel:{job.id}")])
        self.telegram.send_message("\n".join(lines), reply_markup=inline_keyboard(rows))


async def async_main() -> int:
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        await ReminderReviewApplication(settings).run()
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
