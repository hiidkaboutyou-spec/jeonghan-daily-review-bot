from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from .archive_store import ArchiveStore
from .config import ConfigError, Settings
from .main import Application, check_project, parse_date_query, rank_groups, short_id
from .media_file_cache import MediaFileCache
from .models import Draft, Update
from .organizer import organize_updates
from .private_inbox_ui import inbox_draft_keyboard, inbox_list_keyboard
from .private_telegram import PrivateReviewTelegramBot, telegram_file_identity
from .private_ui import date_picker_keyboard, source_page_keyboard
from .review_inbox import ReviewInboxStore
from .style import ensure_rtl_line
from .telegram import TelegramError, draft_keyboard, inline_keyboard, main_keyboard
from .x_client import XCollectionError


class PrivateReviewApplication(Application):
    """Private-review extensions layered on the existing tested application."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        db_path = settings.state_path.with_name("private-review.sqlite3")
        self.archive_db = ArchiveStore(db_path)
        self.media_cache = MediaFileCache(db_path)
        self.inbox = ReviewInboxStore(db_path)
        self.telegram = PrivateReviewTelegramBot(settings.telegram_token, settings.admin_user_id, settings.review_chat_id)
        self.archive_db.sync_from_json(self.state.data.get("archive", {}))
        self.archive_db.sync_drafts(self.state.data.get("drafts", {}))
        self.inbox.sync_from_state(self.state.data.get("drafts", {}), self.state.data.get("archive", {}))

    async def handle_message(self, message):
        if not self.telegram.is_admin_message(message):
            return
        text = str(message.get("text", "") or message.get("caption", "")).strip()
        command = text.partition(" ")[0].split("@", 1)[0].lower()
        if text == "📥 پیش‌نویس‌ها" or command == "/inbox":
            self.show_inbox(status="pending", page=0)
            return
        await super().handle_message(message)

    async def handle_callback(self, callback):
        if not self.telegram.is_admin_callback(callback):
            return
        data = str(callback.get("data", ""))
        callback_id = str(callback.get("id", ""))
        message_id = int(callback.get("message", {}).get("message_id", 0) or 0)
        if data.startswith("inbox:page:"):
            parts = data.split(":")
            status = parts[2] if len(parts) > 2 else "pending"
            try:
                page = int(parts[3])
            except (IndexError, ValueError):
                page = 0
            self._answer_callback_safely(callback_id)
            self.show_inbox(status=status, page=page, message_id=message_id)
            return
        if data.startswith("inbox:open:"):
            parts = data.split(":")
            if len(parts) >= 5:
                draft_id, status = parts[2], parts[3]
                try:
                    page = int(parts[4])
                except ValueError:
                    page = 0
                self._answer_callback_safely(callback_id)
                self.open_inbox_draft(draft_id, status=status, page=page, message_id=message_id)
                return
        if data.startswith("srcpage:"):
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                page = 0
            self._answer_callback_safely(callback_id)
            self.show_sources(page=page, message_id=message_id)
            return
        if data.startswith("datepage:"):
            try:
                offset = int(data.split(":", 1)[1])
            except ValueError:
                offset = 0
            self._answer_callback_safely(callback_id)
            self.show_date_picker(offset_days=offset, message_id=message_id)
            return
        if data.startswith("datepick:"):
            raw = data.split(":", 1)[1]
            if len(raw) == 8 and raw.isdigit():
                query = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
                self.state.pop_awaiting(self.settings.admin_user_id)
                self._answer_callback_safely(callback_id)
                await self.run_search(query)
                return
        if data.startswith("noop:"):
            self._answer_callback_safely(callback_id)
            return
        await super().handle_callback(callback)

    async def handle_draft_action(self, action: str, draft_id: str, message_id: int) -> None:
        await super().handle_draft_action(action, draft_id, message_id)
        if action == "reject":
            self.inbox.set_status(draft_id, "rejected")
        elif action == "copy":
            self.inbox.set_status(draft_id, "ready")
        draft = self.state.get_draft(draft_id)
        if draft is not None:
            self.archive_db.update_caption(draft.update_id, draft.caption)

    def show_inbox(self, *, status: str = "pending", page: int = 0, message_id: int | None = None) -> None:
        status = status if status in {"pending", "ready", "rejected", "all"} else "pending"
        items, page, pages = self.inbox.list_items(status=status, page=page, page_size=5)
        total = self.inbox.count(status)
        text = f"📥 صندوق پیش‌نویس‌های خصوصی — {status} — {total} مورد"
        if not items:
            text += "\n\nچیزی در این فیلتر نیست."
        markup = inbox_list_keyboard(items, status, page, pages)
        if message_id:
            self.telegram.edit_message_text(message_id, text, reply_markup=markup)
        else:
            self.telegram.send_message(text, reply_markup=markup)

    def open_inbox_draft(self, draft_id: str, *, status: str, page: int, message_id: int) -> None:
        draft = self.state.get_draft(draft_id)
        if draft is None:
            self.telegram.edit_message_text(message_id, "این پیش‌نویس دیگر در حافظه نیست.", reply_markup=inline_keyboard([[("◀️ برگشت", f"inbox:page:{status}:{page}")]]))
            return
        item = self.inbox.get(draft_id)
        details = ""
        if item is not None:
            details = f"\n\nوضعیت: {item.status} · منبع: @{item.source or 'unknown'} · دسته: {item.category}"
        text = draft.caption + details
        self.telegram.edit_message_text(message_id, text, reply_markup=inbox_draft_keyboard(draft_id, status, page))

    def _answer_callback_safely(self, callback_id: str) -> None:
        try:
            self.telegram.answer_callback(callback_id, "در حال انجام…")
        except TelegramError:
            pass

    def show_sources(self, page: int = 0, message_id: int | None = None) -> None:
        markup, page, pages = source_page_keyboard(self.settings.sources, page)
        text = f"یک منبع را برای دریافت کامل ۲۴ ساعت انتخاب کن — صفحه {page + 1} از {pages}:"
        if message_id:
            self.telegram.edit_message_text(message_id, text, reply_markup=markup)
        else:
            self.telegram.send_message(text, reply_markup=markup)

    def ask_for_search(self) -> None:
        self.state.set_awaiting(self.settings.admin_user_id, "search")
        self.telegram.send_message(
            ensure_rtl_line("تاریخ یا توضیحت را تایپ کن، یا از تاریخ‌های پایین انتخاب کن.\nمثلاً: 2026-07-14 / 260714 / لایوی که داشت بازی می‌کرد"),
            reply_markup=date_picker_keyboard(datetime.now(self.settings.timezone).date()),
        )

    def show_date_picker(self, offset_days: int = 0, message_id: int | None = None) -> None:
        today = datetime.now(self.settings.timezone).date()
        markup = date_picker_keyboard(today, offset_days)
        text = "📅 یک روز را انتخاب کن، یا توضیح/تاریخ را مستقیم تایپ کن:"
        if message_id:
            self.telegram.edit_message_text(message_id, text, reply_markup=markup)
        else:
            self.telegram.send_message(text, reply_markup=markup)

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        if not force:
            updates = [item for item in updates if not self.state.is_seen(item.id)]
        if not updates:
            return
        for update in updates:
            self.archive_db.index_update(update)
        groups = organize_updates(updates)
        total_updates = sum(len(group.updates) for group in groups)
        self.telegram.send_message(ensure_rtl_line(f"{total_updates} آپدیت در {len(groups)} گروه پیدا شد؛ ارسال از قدیمی به جدید شروع شد."), reply_markup=main_keyboard())
        for group in groups:
            existing_drafts: dict[str, Draft] = {}
            if not force:
                for update in group.updates:
                    draft_id = short_id(f"scheduled:{group.key}:{update.id}")
                    existing = self.state.get_draft(draft_id)
                    if existing is not None:
                        existing_drafts[update.id] = existing

            # A failed send leaves the complete group's drafts behind. On retry we
            # must resume those exact captions without invoking Gemini again. If a
            # legacy/partial state is missing any draft, generate once and persist
            # every missing caption before the first network delivery in the group.
            copy = None
            if force or len(existing_drafts) != len(group.updates):
                copy = self.writer.write_group(group)
                group.title = copy.title or group.title
                if copy.category in self.settings.themes.get("themes", {}):
                    group.category = copy.category
            manual_review = getattr(self.writer, "last_manual_review", {})
            if not isinstance(manual_review, dict):
                manual_review = {}

            prepared: list[tuple[Update, Draft, str | None]] = []
            for part, update in enumerate(group.updates, start=1):
                existing = existing_drafts.get(update.id)
                if existing is not None:
                    draft = existing
                    delivery_key = f"draft:{draft.id}"
                else:
                    assert copy is not None
                    body = copy.bodies.get(update.id) or update.text
                    caption = self.themes.caption(group, update, body, part, len(group.updates))
                    if force:
                        draft_id = short_id(f"force:{update.id}:{datetime.now(timezone.utc).timestamp()}")
                        delivery_key = None
                    else:
                        draft_id = short_id(f"scheduled:{group.key}:{update.id}")
                        delivery_key = f"draft:{draft_id}"
                    draft = Draft(
                        id=draft_id,
                        update_id=update.id,
                        event_key=group.key,
                        caption=caption,
                        mode=(
                            "manual_review"
                            if update.id in manual_review
                            else "default"
                        ),
                        telegram_message_id=0,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self.state.save_draft(draft)
                prepared.append((update, draft, delivery_key))

            # StateStore is normally flushed by Application.run's finally block,
            # but a workflow can be terminated at its execution deadline. Flush the
            # whole immutable group plan before its first Telegram network call.
            self.state.save()

            for update, draft, delivery_key in prepared:
                if update.media:
                    await self._deliver_private_media(update)
                self.state.save_draft(draft)
                sent = self.telegram.send_message(
                    draft.caption,
                    reply_markup=draft_keyboard(draft.id),
                    delivery_key=delivery_key,
                )
                draft.telegram_message_id = int(sent.get("message_id", 0) or 0)
                self.state.save_draft(draft)
                self.state.archive_update(update)
                self.inbox.upsert(draft, update, status="pending")
                self.archive_db.index_update(update, caption=draft.caption)
                self.state.mark_seen(update)
                # A later hard timeout must resume at the first unconfirmed update,
                # not replay work already acknowledged by Telegram.
                self.state.save()

    async def _deliver_private_media(self, update: Update) -> None:
        media = list(update.media[:20])
        cached = self.media_cache.get_all(media)
        if cached is not None and cached:
            try:
                self.telegram.send_cached_media(cached)
                return
            except TelegramError:
                for item in media:
                    self.media_cache.delete(item)
        temp_handles = []
        prepared = []
        prepared_items = []
        try:
            for item in media:
                single = Update.from_dict(update.to_dict())
                single.media = [item]
                temp, values = self.media.prepare(single)
                temp_handles.append(temp)
                if values:
                    prepared.append(values[0])
                    prepared_items.append(item)
            if not prepared:
                return
            sent = self.telegram.send_media(prepared)
            for item, message in zip(prepared_items, sent):
                file_id, unique_id = telegram_file_identity(message, item.kind)
                if file_id:
                    self.media_cache.put(item, file_id, unique_id)
        finally:
            for temp in temp_handles:
                temp.cleanup()

    async def run_search(self, query: str) -> None:
        self.telegram.send_message(f"🔎 اول آرشیو خود بات را برای «{query[:200]}» می‌گردم، بعد در صورت نیاز X را هم چک می‌کنم…", reply_markup=main_keyboard())
        date_range = parse_date_query(query, self.settings.timezone)
        if date_range:
            start, end = date_range
        else:
            start = end = None
        local_updates = self.archive_db.search(query, start=start, end=end, limit=120)
        expanded = self.writer.expand_search(query)
        if date_range:
            base_queries = []
            for group in self.settings.keyword_groups:
                terms = [str(term) for term in group.get("terms", []) if str(term).strip()]
                if terms:
                    base_queries.append(" OR ".join(f'\"{term}\"' if " " in term else term for term in terms))
            expanded = base_queries + expanded
        external_updates = []
        external_error: XCollectionError | None = None
        try:
            external_updates = await self.collector.search_archive(expanded, start=start, end=end, max_per_query=140)
        except XCollectionError as exc:
            external_error = exc
        combined = {item.id: item for item in local_updates}
        for item in external_updates:
            combined[item.id] = item
            self.archive_db.index_update(item)
        updates = list(combined.values())
        if not updates:
            if external_error is not None:
                raise external_error
            self.telegram.send_message("هیچ نتیجهٔ قابل‌استفاده‌ای پیدا نشد.", reply_markup=main_keyboard())
            return
        candidate_limit = max(1, min(8, int(self.settings.runtime.get("max_search_candidates", 8))))
        groups = rank_groups(query, organize_updates(updates))[:candidate_limit]
        titles = self.writer.candidate_titles(query, groups)
        session_id = short_id(query + datetime.now(timezone.utc).isoformat())
        self.state.create_session(session_id, {"query": query, "candidates": [{"key": group.key, "title": titles.get(group.key) or group.title, "started_at": group.started_at.isoformat(), "selected": group.updates[0].to_dict(), "preview_ids": [item.id for item in group.updates]} for group in groups]})
        local_ids = {item.id for item in local_updates}
        lines = [f"نتیجه‌های پیشنهادی برای «{query}»:"]
        rows = []
        for index, group in enumerate(groups):
            local_date = group.started_at.astimezone(self.settings.timezone).strftime("%Y-%m-%d %H:%M")
            title = titles.get(group.key) or group.title
            origin = "آرشیو/‏X" if any(item.id in local_ids for item in group.updates) else "X"
            lines.append(f"{index + 1}. {title} — {local_date} — {len(group.updates)} مورد — {origin}")
            rows.append([(f"{index + 1}. {title[:40]}", f"pick:{session_id}:{index}")])
        self.telegram.send_message(ensure_rtl_line("\n".join(lines)), reply_markup=inline_keyboard(rows))


async def async_main() -> int:
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        await PrivateReviewApplication(settings).run()
        return 0
    except ConfigError as exc:
        import logging
        logging.getLogger(__name__).error("Configuration error: %s", exc)
        return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check_project()
    return asyncio.run(async_main())
