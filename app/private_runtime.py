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
from .private_telegram import PrivateReviewTelegramBot, telegram_file_identity
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
        self.telegram = PrivateReviewTelegramBot(
            settings.telegram_token,
            settings.admin_user_id,
            settings.review_chat_id,
        )
        self.archive_db.sync_from_json(self.state.data.get("archive", {}))
        self.archive_db.sync_drafts(self.state.data.get("drafts", {}))

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        if not force:
            updates = [item for item in updates if not self.state.is_seen(item.id)]
        if not updates:
            return
        for update in updates:
            self.archive_db.index_update(update)
        groups = organize_updates(updates)
        total_updates = sum(len(group.updates) for group in groups)
        self.telegram.send_message(
            ensure_rtl_line(f"{total_updates} آپدیت در {len(groups)} گروه پیدا شد؛ ارسال از قدیمی به جدید شروع شد."),
            reply_markup=main_keyboard(),
        )
        for group in groups:
            copy = self.writer.write_group(group)
            group.title = copy.title or group.title
            if copy.category in self.settings.themes.get("themes", {}):
                group.category = copy.category
            for part, update in enumerate(group.updates, start=1):
                body = copy.bodies.get(update.id) or update.text
                caption = self.themes.caption(group, update, body, part, len(group.updates))
                if update.media:
                    await self._deliver_private_media(update)
                draft_id = short_id(f"{update.id}:{datetime.now(timezone.utc).timestamp()}")
                sent = self.telegram.send_message(caption, reply_markup=draft_keyboard(draft_id))
                self.state.archive_update(update)
                self.state.save_draft(
                    Draft(
                        id=draft_id,
                        update_id=update.id,
                        event_key=group.key,
                        caption=caption,
                        mode="default",
                        telegram_message_id=int(sent.get("message_id", 0) or 0),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                self.archive_db.index_update(update, caption=caption)
                self.state.mark_seen(update)

    async def _deliver_private_media(self, update: Update) -> None:
        media = list(update.media[:20])
        cached = self.media_cache.get_all(media)
        if cached is not None and cached:
            try:
                self.telegram.send_cached_media(cached)
                return
            except TelegramError:
                # file_id can become unusable; never make delivery depend on cache.
                for item in media:
                    self.media_cache.delete(item)

        temp_handles = []
        prepared = []
        prepared_items = []
        try:
            # Prepare each source item separately so returned Telegram messages map
            # exactly back to the original URL even if another item fails to prepare.
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
        self.telegram.send_message(
            f"🔎 اول آرشیو خود بات را برای «{query[:200]}» می‌گردم، بعد در صورت نیاز X را هم چک می‌کنم…",
            reply_markup=main_keyboard(),
        )
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
