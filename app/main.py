from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .ai import CaptionWriter
from .config import ConfigError, ROOT, Settings
from .media import MediaManager
from .models import Draft, EventGroup, Update
from .organizer import organize_updates
from .state import StateStore
from .style import StyleMemory, ThemeEngine, ensure_rtl_line
from .telegram import TelegramBot, TelegramError, draft_keyboard, inline_keyboard, main_keyboard
from .translation_safety import translation_unavailable
from .x_client import XCollectionError, XCollector, normalize_handle

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class Application:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.state = StateStore(settings.state_path)
        self.telegram = TelegramBot(settings.telegram_token, settings.admin_user_id, settings.review_chat_id)
        self.memory = StyleMemory(ROOT)
        self.writer = CaptionWriter(settings.gemini_api_key, settings.gemini_model, self.memory)
        self.themes = ThemeEngine(settings.themes, settings.timezone)
        self.collector = XCollector(settings.x_cookies, settings.sources, settings.keyword_groups)
        self.media = MediaManager(settings.x_cookies)

    async def run(self) -> None:
        try:
            self._ensure_polling_mode_periodically()
            await self.process_telegram_updates()
            await self.run_scheduled_scan()
            await self.deliver_pending()
        finally:
            self.state.save()

    def _ensure_polling_mode_periodically(self) -> None:
        """Clear stale webhooks at most once per day so getUpdates remains valid."""
        now = datetime.now(timezone.utc)
        checked = _parse_state_datetime(self.state.data.get("polling_mode_checked"))
        if checked and now - checked < timedelta(hours=24):
            return
        self.telegram.ensure_polling_mode()
        self.state.data["polling_mode_checked"] = now.isoformat()

    async def process_telegram_updates(self) -> None:
        updates = self.telegram.get_updates(self.state.telegram_offset)
        for item in updates:
            update_id = int(item.get("update_id", 0))
            self.state.telegram_offset = max(self.state.telegram_offset, update_id + 1)
            try:
                if "message" in item:
                    await self.handle_message(item["message"])
                elif "callback_query" in item:
                    await self.handle_callback(item["callback_query"])
            except (XCollectionError, TelegramError, ConfigError) as exc:
                logger.warning("Command failed: %s", exc)
                self._safe_send(f"❌ {str(exc)[:900]}")
            except Exception as exc:
                logger.exception("Unexpected command error")
                self._safe_send(f"❌ خطای پیش‌بینی‌نشده: {type(exc).__name__}")

    async def handle_message(self, message: dict[str, Any]) -> None:
        if not self.telegram.is_admin_message(message):
            return
        text = str(message.get("text", "") or message.get("caption", "")).strip()
        if not text:
            return

        if text == "📚 فن‌فیک":
            self.telegram.send_message(
                "📚 دارم هر دو لیست X و AO3 را آماده می‌کنم؛ ممکن است یکی دو دقیقه طول بکشد…",
                reply_markup=main_keyboard(),
            )
            from .fic_digest import send_digests

            await send_digests(self.settings, self.telegram)
            return

        button_map = {
            "🕑 ۲ ساعت اخیر": "/recent2h",
            "🗂 ۲۴ ساعت منبع": "/sources",
            "🔎 سرچ آرشیو": "/search",
            "📋 وضعیت": "/status",
            "❔ راهنما": "/help",
        }
        text = button_map.get(text, text)

        awaiting = self.state.pop_awaiting(self.settings.admin_user_id)
        if awaiting == "search" and not text.startswith("/"):
            await self.run_search(text)
            return
        if awaiting == "source" and not text.startswith("/"):
            await self.run_source24(text)
            return

        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        argument = argument.strip()
        if command in {"/start", "/menu"}:
            self.send_start()
        elif command in {"/recent2h", "/fetch2h"}:
            await self.run_recent2h()
        elif command == "/search":
            if argument:
                await self.run_search(argument)
            else:
                self.ask_for_search()
        elif command in {"/source24", "/fetch24h"}:
            if argument:
                await self.run_source24(argument)
            else:
                self.show_sources()
        elif command == "/sources":
            self.show_sources()
        elif command == "/status":
            self.send_status()
        elif command == "/help":
            self.send_help()
        elif command == "/fic":
            from .fic_digest import send_digests

            await send_digests(self.settings, self.telegram)
        else:
            self.telegram.send_message(
                "دستور را نشناختم. از دکمه‌های پایین چت استفاده کن:",
                reply_markup=main_keyboard(),
            )

    async def handle_callback(self, callback: dict[str, Any]) -> None:
        if not self.telegram.is_admin_callback(callback):
            return
        callback_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        try:
            self.telegram.answer_callback(callback_id, "در حال انجام…")
        except TelegramError as exc:
            # Telegram callbacks expire quickly; an expired acknowledgement must not
            # prevent the actual requested action from running.
            logger.info("Callback acknowledgement failed; continuing action: %s", exc)
        parts = data.split(":")
        if data == "cmd:recent2h":
            await self.run_recent2h()
        elif data == "cmd:search":
            self.ask_for_search()
        elif data == "cmd:sources":
            self.show_sources()
        elif data == "cmd:status":
            self.send_status()
        elif data == "cmd:help":
            self.send_help()
        elif len(parts) >= 3 and parts[0] == "source":
            await self.run_source24(parts[2])
        elif len(parts) >= 3 and parts[0] == "pick":
            try:
                index = int(parts[2])
            except ValueError:
                self.telegram.send_message("گزینهٔ انتخاب‌شده معتبر نیست.", reply_markup=main_keyboard())
                return
            await self.run_selected_event(parts[1], index)
        elif len(parts) >= 3 and parts[0] == "draft":
            message_id = int(callback.get("message", {}).get("message_id", 0) or 0)
            await self.handle_draft_action(parts[1], parts[2], message_id)

    def send_start(self) -> None:
        text = (
            "بات خصوصی دیلی جونگهان آماده است.\n\n"
            "دکمه‌های ضروری همیشه پایین چت می‌مانند؛ لازم نیست دستورها را حفظ کنی.\n"
            "• ۲ ساعت اخیر — همهٔ آپدیت‌ها حتی اگر قبلاً فرستاده شده باشند\n"
            "• ۲۴ ساعت منبع — انتخاب یک منبع و دریافت کامل\n"
            "• سرچ آرشیو — تاریخ یا توضیح رویداد\n"
            "• فن‌فیک — همان لحظه دو لیست جدا از X و AO3\n"
            "• وضعیت و راهنما"
        )
        self.telegram.send_message(ensure_rtl_line(text), reply_markup=main_keyboard())

    def ask_for_search(self) -> None:
        self.state.set_awaiting(self.settings.admin_user_id, "search")
        self.telegram.send_message(
            ensure_rtl_line(
                "تاریخ یا توضیحت را بفرست؛ مثلاً:\n2026-07-14\n260714\nلایوی که داشت بازی می‌کرد و با خودش حرف می‌زد"
            ),
            reply_markup=main_keyboard(),
        )

    def show_sources(self) -> None:
        enabled = [source for source in self.settings.sources if source.get("enabled", True)]
        rows = [[(f"@{source['handle']}", f"source:24:{source['handle']}")] for source in enabled]
        rows.append([("➕ وارد کردن منبع دیگر", "source:24:custom")])
        self.telegram.send_message(
            "یک منبع را برای دریافت کامل ۲۴ ساعت انتخاب کن:",
            reply_markup=inline_keyboard(rows),
        )

    def send_status(self) -> None:
        data = self.state.data
        text = (
            f"وضعیت بات\n\n"
            f"منابع فعال: {sum(bool(item.get('enabled', True)) for item in self.settings.sources)}\n"
            f"آیتم‌های آرشیو داخلی: {len(data.get('archive', {}))}\n"
            f"صف باقی‌مانده: {len(data.get('pending_delivery', []))}\n"
            f"آخرین اسکن موفق خودکار: {data.get('last_auto_run') or 'هنوز اجرا نشده'}\n"
            f"مدل کپشن: {self.settings.gemini_model}"
        )
        self.telegram.send_message(ensure_rtl_line(text), reply_markup=main_keyboard())

    def send_help(self) -> None:
        text = (
            "🕑 ۲ ساعت اخیر — تمام محتوای دو ساعت اخیر، حتی تکراری\n"
            "🗂 ۲۴ ساعت منبع — انتخاب منبع و دریافت کامل\n"
            "🔎 سرچ آرشیو — تاریخ یا توضیح رویداد\n"
            "📚 فن‌فیک — اجرای فوری دو لیست X و AO3\n"
            "📋 وضعیت — وضعیت بات\n"
            "❔ راهنما — همین توضیح"
        )
        self.telegram.send_message(ensure_rtl_line(text), reply_markup=main_keyboard())

    async def run_recent2h(self) -> None:
        self.telegram.send_message(
            "🕑 دارم تمام آپدیت‌های دو ساعت اخیر را دوباره جمع می‌کنم…",
            reply_markup=main_keyboard(),
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        updates = await self.collector.collect_window(start, end, max_per_query=200)
        updates = sorted(
            (item for item in updates if start <= item.created_at < end),
            key=lambda item: (item.created_at, item.id),
        )
        ceiling = max(1, int(self.settings.runtime.get("max_collection_items", 1000)))
        updates = updates[:ceiling]
        if not updates:
            self.telegram.send_message("در دو ساعت اخیر چیزی پیدا نشد.", reply_markup=main_keyboard())
            return
        await self.deliver_updates(updates, force=True)

    async def run_source24(self, value: str) -> None:
        if value == "custom":
            self.state.set_awaiting(self.settings.admin_user_id, "source")
            self.telegram.send_message("لینک X یا یوزرنیم منبع را بفرست.", reply_markup=main_keyboard())
            return
        handle = normalize_handle(value)
        if not handle:
            self.telegram.send_message("یوزرنیم منبع درست نیست.", reply_markup=main_keyboard())
            return
        self.telegram.send_message(
            f"🗂 دارم ۲۴ ساعت کامل @{handle} را از قدیمی به جدید می‌گیرم…",
            reply_markup=main_keyboard(),
        )
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=24)
        updates = await self.collector.collect_source(handle, start, end)
        updates = sorted(
            (item for item in updates if start <= item.created_at < end),
            key=lambda item: (item.created_at, item.id),
        )
        ceiling = max(1, int(self.settings.runtime.get("max_collection_items", 1000)))
        updates = updates[:ceiling]
        if not updates:
            self.telegram.send_message(
                f"برای @{handle} در ۲۴ ساعت گذشته چیزی پیدا نشد.",
                reply_markup=main_keyboard(),
            )
            return
        await self.deliver_updates(updates, force=True)

    async def run_search(self, query: str) -> None:
        self.telegram.send_message(
            f"🔎 دارم برای «{query[:200]}» گزینه‌های مرتبط را پیدا می‌کنم…",
            reply_markup=main_keyboard(),
        )
        date_range = parse_date_query(query, self.settings.timezone)
        expanded = self.writer.expand_search(query)
        if date_range:
            start, end = date_range
            base_queries = []
            for group in self.settings.keyword_groups:
                terms = [str(term) for term in group.get("terms", []) if str(term).strip()]
                if terms:
                    base_queries.append(
                        " OR ".join(f'\"{term}\"' if " " in term else term for term in terms)
                    )
            expanded = base_queries + expanded
        else:
            start = end = None
        updates = await self.collector.search_archive(
            expanded,
            start=start,
            end=end,
            max_per_query=140,
        )
        if not updates:
            self.telegram.send_message(
                "هیچ نتیجهٔ واقعی و قابل‌استفاده‌ای پیدا نشد.",
                reply_markup=main_keyboard(),
            )
            return
        candidate_limit = max(1, min(8, int(self.settings.runtime.get("max_search_candidates", 8))))
        groups = rank_groups(query, organize_updates(updates))[:candidate_limit]
        titles = self.writer.candidate_titles(query, groups)
        session_id = short_id(query + datetime.now(timezone.utc).isoformat())
        self.state.create_session(
            session_id,
            {
                "query": query,
                "candidates": [
                    {
                        "key": group.key,
                        "title": titles.get(group.key) or group.title,
                        "started_at": group.started_at.isoformat(),
                        "selected": group.updates[0].to_dict(),
                        "preview_ids": [item.id for item in group.updates],
                    }
                    for group in groups
                ],
            },
        )
        lines = [f"نتیجه‌های پیشنهادی برای «{query}»: "]
        rows: list[list[tuple[str, str]]] = []
        for index, group in enumerate(groups):
            local_date = group.started_at.astimezone(self.settings.timezone).strftime("%Y-%m-%d %H:%M")
            title = titles.get(group.key) or group.title
            lines.append(f"{index + 1}. {title} — {local_date} — {len(group.updates)} مورد")
            rows.append([(f"{index + 1}. {title[:40]}", f"pick:{session_id}:{index}")])
        self.telegram.send_message(
            ensure_rtl_line("\n".join(lines)),
            reply_markup=inline_keyboard(rows),
        )

    async def run_selected_event(self, session_id: str, index: int) -> None:
        session = self.state.get_session(session_id)
        if not session:
            self.telegram.send_message("این سرچ منقضی شده؛ دوباره سرچ کن.", reply_markup=main_keyboard())
            return
        candidates = list(session.get("candidates", []))
        if index < 0 or index >= len(candidates):
            self.telegram.send_message("گزینهٔ انتخاب‌شده معتبر نیست.", reply_markup=main_keyboard())
            return
        try:
            selected = Update.from_dict(candidates[index]["selected"])
        except (KeyError, TypeError, ValueError):
            self.telegram.send_message("دادهٔ این سرچ خراب شده؛ دوباره سرچ کن.", reply_markup=main_keyboard())
            return
        self.telegram.send_message(
            "انتخاب شد؛ دارم تمام رشته و آپدیت‌های مرتبط همان رویداد را جمع می‌کنم…",
            reply_markup=main_keyboard(),
        )
        updates = await self.collector.collect_event(selected)
        await self.deliver_updates(updates or [selected], force=True)

    async def run_scheduled_scan(self) -> None:
        now = datetime.now(timezone.utc)
        last = _parse_state_datetime(self.state.data.get("last_auto_run")) or (now - timedelta(hours=2))
        if now - last < timedelta(minutes=10):
            return
        lookback = max(2, int(self.settings.runtime.get("scheduled_lookback_hours", 24)))
        start = max(last - timedelta(minutes=30), now - timedelta(hours=lookback))
        try:
            updates = await self.collector.collect_window(start, now, max_per_query=200)
        except XCollectionError as exc:
            logger.warning("Scheduled X scan failed: %s", exc)
            self._notify_x_failure_if_due(now)
            # Do not advance last_auto_run on failure. The next successful run must
            # retry the missed time window (bounded by scheduled_lookback_hours).
            return

        fresh = [item for item in updates if not self.state.is_seen(item.id)]
        fresh.sort(key=lambda item: (item.created_at, item.id))
        ceiling = max(1, int(self.settings.runtime.get("max_collection_items", 1000)))
        self.state.queue_updates(fresh[:ceiling], force=False)

        # collect_window can return useful partial data while one source/query failed.
        # Queue what we got, but don't advance the success cursor until every configured
        # retrieval path completed. Seen IDs prevent duplicates on the retry.
        if getattr(self.collector, "last_errors", []):
            logger.warning("Scheduled X scan returned partial results; cursor retained for retry.")
            self._notify_x_failure_if_due(now)
            return
        self.state.data["last_auto_run"] = now.isoformat()
        self.state.data["last_x_error_notice"] = ""

    def _notify_x_failure_if_due(self, now: datetime) -> None:
        last_notice = _parse_state_datetime(self.state.data.get("last_x_error_notice"))
        if last_notice and now - last_notice < timedelta(hours=2):
            return
        self._safe_send("⚠️ اسکن خودکار X کامل نشد؛ زمان آخرین اسکن موفق حفظ شد و اجرای بعدی دوباره بازهٔ جاافتاده را بررسی می‌کند.")
        self.state.data["last_x_error_notice"] = now.isoformat()

    async def deliver_pending(self) -> None:
        outage_notice = _parse_state_datetime(self.state.data.get("translation_outage_notice"))
        if outage_notice and datetime.now(timezone.utc) - outage_notice < timedelta(minutes=12):
            # A workflow process is restarted every few seconds during its live
            # window. Do not hammer a provider that just reported an outage; the
            # next scheduled workflow will retry the untouched queue.
            return
        limit = max(1, int(self.settings.runtime.get("max_auto_items_per_run", 1000)))
        pending = self.state.pop_pending(limit)
        if not pending:
            return
        # Preserve force semantics if future callers enqueue forced replay items.
        batch: list[Update] = []
        current_force: bool | None = None
        for item, force in pending:
            if current_force is None:
                current_force = force
            if force != current_force and batch:
                await self.deliver_updates(batch, force=current_force)
                batch = []
                current_force = force
            batch.append(item)
        if batch:
            await self.deliver_updates(batch, force=bool(current_force))

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        if not force:
            updates = [item for item in updates if not self.state.is_seen(item.id)]
        if not updates:
            return
        groups = organize_updates(updates)
        total_updates = sum(len(group.updates) for group in groups)
        self.telegram.send_message(
            ensure_rtl_line(f"{total_updates} آپدیت در {len(groups)} گروه پیدا شد؛ ارسال از قدیمی به جدید شروع شد."),
            reply_markup=main_keyboard(),
        )
        deferred = 0
        for group in groups:
            copy = self.writer.write_group(group)
            manual_review = getattr(self.writer, "last_manual_review", {})
            if not isinstance(manual_review, dict):
                manual_review = {}
            group.title = copy.title or group.title
            if copy.category in self.settings.themes.get("themes", {}):
                group.category = copy.category
            for part, update in enumerate(group.updates, start=1):
                body = copy.bodies.get(update.id) or update.text
                if translation_unavailable(body):
                    deferred += 1
                    # Do not mark it seen: the pending queue will retry it when the
                    # translation provider is healthy again.
                    continue
                caption = self.themes.caption(group, update, body, part, len(group.updates))
                temp, prepared = self.media.prepare(update)
                try:
                    if prepared:
                        self.telegram.send_media(prepared)
                finally:
                    temp.cleanup()
                draft_id = short_id(f"{update.id}:{datetime.now(timezone.utc).timestamp()}")
                sent = self.telegram.send_message(caption, reply_markup=draft_keyboard(draft_id))
                self.state.archive_update(update)
                self.state.save_draft(
                    Draft(
                        id=draft_id,
                        update_id=update.id,
                        event_key=group.key,
                        caption=caption,
                        mode=(
                            "manual_review"
                            if update.id in manual_review
                            else "default"
                        ),
                        telegram_message_id=int(sent.get("message_id", 0) or 0),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                self.state.mark_seen(update)
        if deferred:
            self._notify_translation_outage_if_due(deferred)

    def _notify_translation_outage_if_due(self, count: int) -> None:
        now = datetime.now(timezone.utc)
        previous = _parse_state_datetime(self.state.data.get("translation_outage_notice"))
        if previous and now - previous < timedelta(hours=2):
            return
        self._safe_send(
            f"⚠️ سرویس ترجمه در دسترس نبود؛ {count} آپدیت خام ارسال نشد و برای تلاش بعدی در صف ماند."
        )
        self.state.data["translation_outage_notice"] = now.isoformat()

    async def handle_draft_action(self, action: str, draft_id: str, message_id: int) -> None:
        draft = self.state.get_draft(draft_id)
        if not draft:
            self.telegram.send_message("این پیش‌نویس دیگر در حافظه نیست.", reply_markup=main_keyboard())
            return
        if action == "copy":
            self.telegram.send_message(draft.caption, reply_markup=main_keyboard())
            return
        if action == "reject":
            self.telegram.edit_message_text(
                message_id,
                "🗑 رد شد\n\n" + draft.caption,
                reply_markup=inline_keyboard([]),
            )
            return
        mode = {"fun": "funnier", "soft": "softer", "precise": "precise"}.get(action)
        if not mode:
            return
        update = self.state.get_update(draft.update_id)
        if update is None:
            self.telegram.send_message("متن اصلی این پیش‌نویس پیدا نشد.", reply_markup=main_keyboard())
            return
        group = organize_updates([update])[0]
        copy = self.writer.write_group(group, mode=mode)
        group.title = copy.title or group.title
        body = copy.bodies.get(update.id) or update.text
        caption = self.themes.caption(group, update, body, 1, 1)
        self.telegram.edit_message_text(message_id, caption, reply_markup=draft_keyboard(draft_id))
        draft.caption = caption
        draft.mode = mode
        self.state.save_draft(draft)

    def _safe_send(self, text: str) -> None:
        try:
            self.telegram.send_message(text, reply_markup=main_keyboard())
        except Exception:
            logger.exception("Could not send error message to Telegram")


def _parse_state_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date_query(query: str, timezone_info=timezone.utc) -> tuple[datetime, datetime] | None:
    query = query.strip()
    patterns = [r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", r"\b(\d{2})(\d{2})(\d{2})\b"]
    for index, pattern in enumerate(patterns):
        match = re.search(pattern, query)
        if not match:
            continue
        if index == 0:
            year, month, day = map(int, match.groups())
        else:
            yy, month, day = map(int, match.groups())
            year = 2000 + yy
        try:
            local_start = datetime(year, month, day, tzinfo=timezone_info)
        except ValueError:
            return None
        local_end = local_start + timedelta(days=1)
        return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)
    return None


def rank_groups(query: str, groups: list[EventGroup]) -> list[EventGroup]:
    tokens = {
        token.casefold()
        for token in re.findall(r"[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", query)
        if len(token) > 1
    }

    def score(group: EventGroup) -> tuple[float, datetime]:
        haystack = " ".join([group.title, *(item.text for item in group.updates)]).casefold()
        overlap = sum(1 for token in tokens if token in haystack)
        media_bonus = sum(bool(item.media) for item in group.updates) * 0.15
        size_bonus = min(len(group.updates), 10) * 0.08
        return overlap + media_bonus + size_bonus, group.started_at

    return sorted(groups, key=score, reverse=True)


def short_id(value: str) -> str:
    # Stable non-security identifier; SHA-1 is retained for persisted draft IDs.
    return hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def check_project() -> int:
    try:
        settings = Settings.load(require_secrets=False)
    except ConfigError as exc:
        print(f"CHECK FAILED: {exc}")
        return 1
    errors = settings.validate_files()
    memory = StyleMemory(ROOT)
    if not memory.samples:
        errors.append("data/channel_memory.jsonl is empty.")
    if not memory.profile:
        errors.append("data/channel_voice_profile.json is empty.")
    if errors:
        for error in errors:
            print("CHECK FAILED:", error)
        return 1
    print(f"CHECK OK: {len(settings.sources)} sources, {len(memory.samples)} style samples")
    return 0


async def async_main() -> int:
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        await Application(settings).run()
        return 0
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check_project()
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
