from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.main import Application
from app.models import MediaItem, Update
from app.private_runtime import PrivateReviewApplication
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant


class ConfiguredSourceRuntimeTests(unittest.TestCase):
    @staticmethod
    def _update(
        id_: str,
        author: str,
        *,
        minutes_ago: int = 10,
        media: bool = False,
    ) -> Update:
        items = (
            [MediaItem(kind="video", url=f"https://video.example/{id_}.mp4", content_type="video/mp4")]
            if media
            else []
        )
        return Update(
            id=id_,
            url=f"https://x.com/{author}/status/{id_}",
            author=author,
            author_name=author,
            text=f"post {id_}",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            media=items,
        )

    @staticmethod
    def _filter(updates):
        chosen = {item.id: item for item in updates if item.author.casefold() == "trustedsource"}
        return sorted(chosen.values(), key=lambda item: (item.created_at, item.id))

    def test_legacy_recent2h_filters_external_hits_and_preserves_oldest_first(self):
        external = self._update("x", "randomfan", minutes_ago=20)
        newer = self._update("2", "trustedsource", minutes_ago=5)
        older = self._update("1", "trustedsource", minutes_ago=30)
        app = object.__new__(Application)
        app.collector = SimpleNamespace(
            collect_window=AsyncMock(return_value=[external, newer, older]),
            filter_configured_updates=self._filter,
            last_errors=[],
        )
        app.telegram = SimpleNamespace(send_message=Mock())
        app.deliver_updates = AsyncMock()

        asyncio.run(app.run_recent2h())

        delivered = app.deliver_updates.await_args.args[0]
        self.assertEqual([item.id for item in delivered], ["1", "2"])
        app.deliver_updates.assert_awaited_once_with(delivered, force=True)

    def test_legacy_source24_delivers_entire_complete_window_without_legacy_cap(self):
        items = [self._update(str(i), "trustedsource", minutes_ago=5) for i in range(1005)]
        app = object.__new__(Application)
        app.collector = SimpleNamespace(
            is_configured_source=lambda handle: str(handle).casefold() == "trustedsource",
            collect_source=AsyncMock(return_value=items),
            filter_configured_updates=self._filter,
        )
        app.settings = SimpleNamespace(runtime={"max_collection_items": 1})
        app.telegram = SimpleNamespace(send_message=Mock())
        app.deliver_updates = AsyncMock()

        asyncio.run(app.run_source24("trustedsource"))

        delivered = app.deliver_updates.await_args.args[0]
        self.assertEqual(len(delivered), 1005)
        app.deliver_updates.assert_awaited_once_with(delivered, force=True)

    def test_legacy_scheduled_partial_queues_all_safe_items_but_keeps_cursor(self):
        now = datetime.now(timezone.utc)
        previous = (now - timedelta(hours=1)).isoformat()
        items = [self._update(str(i), "trustedsource", minutes_ago=5) for i in range(1005)]
        app = object.__new__(Application)
        app.collector = SimpleNamespace(
            collect_window=AsyncMock(return_value=items),
            filter_configured_updates=self._filter,
            last_errors=["@trustedsource incomplete"],
        )
        app.settings = SimpleNamespace(runtime={"scheduled_lookback_hours": 24})
        app.state = SimpleNamespace(
            data={"last_auto_run": previous},
            is_seen=Mock(return_value=False),
            queue_updates=Mock(),
        )
        app._record_x_scan_failure = Mock()

        asyncio.run(app.run_scheduled_scan())

        queued = app.state.queue_updates.call_args.args[0]
        self.assertEqual(len(queued), 1005)
        self.assertEqual(app.state.data["last_auto_run"], previous)
        app._record_x_scan_failure.assert_called_once()

    def test_stale_external_pending_item_is_purged_before_delivery(self):
        external = self._update("outside", "randomfan")
        trusted = self._update("inside", "trustedsource")
        queue = [external.to_dict(), trusted.to_dict()]
        state = SimpleNamespace(
            data={"pending_delivery": queue},
            pop_pending=Mock(return_value=[]),
        )
        app = object.__new__(Application)
        app.state = state
        app.settings = SimpleNamespace(runtime={"max_auto_items_per_run": 60})
        app.collector = SimpleNamespace(
            is_configured_source=lambda handle: str(handle).casefold() == "trustedsource",
            filter_configured_updates=self._filter,
        )

        asyncio.run(app.deliver_pending())

        self.assertEqual([item["id"] for item in state.data["pending_delivery"]], ["inside"])

    def test_private_delivery_blocks_external_media_before_media_pipeline(self):
        external = self._update("outside", "randomfan", media=True)
        app = object.__new__(PrivateReviewApplication)
        app.collector = SimpleNamespace(filter_configured_updates=self._filter)
        app.media = Mock()
        app.telegram = Mock()

        asyncio.run(app.deliver_updates([external], force=True))

        app.media.assert_not_called()
        app.telegram.assert_not_called()

    def test_private_archive_search_filters_historical_external_rows(self):
        external = self._update("outside", "randomfan")
        app = object.__new__(PrivateReviewApplication)
        app.archive_db = SimpleNamespace(search=Mock(return_value=[external]), index_update=Mock())
        app.collector = SimpleNamespace(
            filter_configured_updates=self._filter,
            search_archive=AsyncMock(return_value=[]),
        )
        app.telegram = SimpleNamespace(send_message=Mock())
        app.writer = SimpleNamespace(expand_search=Mock(return_value=["JEONGHAN"]))
        app.settings = SimpleNamespace(
            timezone=timezone.utc,
            keyword_groups=[],
            runtime={"max_search_candidates": 8},
        )

        asyncio.run(app.run_search("JEONGHAN"))

        self.assertTrue(app.archive_db.search.called)
        self.assertTrue(app.collector.search_archive.awaited)
        sent_texts = [str(call.args[0]) for call in app.telegram.send_message.call_args_list]
        self.assertTrue(any("هیچ نتیجه" in text for text in sent_texts))

    def test_webhook_24h_rejects_unconfigured_source_without_fetch(self):
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.collector = SimpleNamespace(
            is_configured_source=lambda handle: str(handle).casefold() == "trustedsource",
            collect_source=AsyncMock(),
        )
        app.telegram = SimpleNamespace(send_message=Mock())

        asyncio.run(app.run_source24("randomfan"))

        app.collector.collect_source.assert_not_awaited()
        self.assertTrue(app.telegram.send_message.called)


if __name__ == "__main__":
    unittest.main()
