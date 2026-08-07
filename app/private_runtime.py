from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from .archive_store import ArchiveStore
from .config import ConfigError, Settings
from .main import Application, check_project, parse_date_query, rank_groups, short_id
from .organizer import organize_updates
from .style import ensure_rtl_line
from .telegram import inline_keyboard, main_keyboard
from .x_client import XCollectionError


class PrivateReviewApplication(Application):
    """Private-review extensions layered on the existing tested application."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.archive_db = ArchiveStore(settings.state_path.with_name("private-review.sqlite3"))
        self.archive_db.sync_from_json(self.state.data.get("archive", {}))
        self.archive_db.sync_drafts(self.state.data.get("drafts", {}))

    async def deliver_updates(self, updates, *, force: bool) -> None:
        for update in updates:
            self.archive_db.index_update(update)
        await super().deliver_updates(updates, force=force)
        self.archive_db.sync_drafts(self.state.data.get("drafts", {}))

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
            external_updates = await self.collector.search_archive(
                expanded,
                start=start,
                end=end,
                max_per_query=140,
            )
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
        lines = [f"نتیجه‌های پیشنهادی برای «{query}»:"]
        rows = []
        for index, group in enumerate(groups):
            local_date = group.started_at.astimezone(self.settings.timezone).strftime("%Y-%m-%d %H:%M")
            title = titles.get(group.key) or group.title
            origin = "آرشیو/‏X" if any(item.id in {u.id for u in local_updates} for item in group.updates) else "X"
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
