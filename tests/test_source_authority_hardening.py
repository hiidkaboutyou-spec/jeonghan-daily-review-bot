from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import MediaItem, Update
from app.x_client import XCollectionError, XCollector
from app.x_completeness import CompleteWindowXCollector


class SourceAuthorityHardeningTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        self.start = self.now - timedelta(hours=2)
        self.collector = XCollector(
            {},
            [
                {
                    "handle": "trustedsource",
                    "enabled": True,
                    "include_replies": True,
                    "jeonghan_only": False,
                }
            ],
            [{"name": "english", "terms": ["JEONGHAN"]}],
        )

    def _update(
        self,
        id_: str,
        author: str,
        text: str,
        *,
        media_kind: str = "",
        minutes_ago: int = 10,
        reply: bool = False,
    ) -> Update:
        media = []
        if media_kind == "video":
            media = [
                MediaItem(
                    kind="video",
                    url=f"https://video.example/{id_}.mp4",
                    content_type="video/mp4",
                )
            ]
        elif media_kind == "photo":
            media = [
                MediaItem(
                    kind="photo",
                    url=f"https://img.example/{id_}.jpg",
                    content_type="image/jpeg",
                )
            ]
        return Update(
            id=id_,
            url=f"https://x.com/{author}/status/{id_}",
            author=author,
            author_name=author,
            text=text,
            created_at=self.now - timedelta(minutes=minutes_ago),
            conversation_id="thread" if reply else id_,
            reply_to_id="root" if reply else "",
            is_reply=reply,
            media=media,
        )

    def test_configured_source_video_survives_without_jeonghan_keyword(self):
        update = self._update(
            "video",
            "trustedsource",
            "new clip ✨",
            media_kind="video",
        )
        kept = self.collector._filter_relevant([update])
        self.assertEqual([item.id for item in kept], ["video"])
        self.assertEqual(kept[0].media[0].kind, "video")

    def test_configured_source_photo_survives_without_jeonghan_keyword(self):
        update = self._update(
            "photo",
            "trustedsource",
            "",
            media_kind="photo",
        )
        kept = self.collector._filter_relevant([update])
        self.assertEqual([item.id for item in kept], ["photo"])
        self.assertEqual(kept[0].media[0].kind, "photo")

    def test_configured_source_text_post_about_another_member_survives(self):
        update = self._update("other-member", "trustedsource", "MINGYU at the airport")
        kept = self.collector._filter_relevant([update])
        self.assertEqual([item.id for item in kept], ["other-member"])

    def test_external_keyword_hit_is_never_relevant_output(self):
        external = self._update("external", "randomfan", "JEONGHAN update")
        self.assertEqual(self.collector._filter_relevant([external]), [])

    def test_configured_source_reply_survives_when_include_replies_is_true(self):
        reply = self._update("reply", "trustedsource", "thanks!", reply=True)
        self.collector._collect_source_timeline = AsyncMock(return_value=[reply])
        self.collector._run_queries = AsyncMock()

        result = asyncio.run(
            self.collector.collect_window(self.start, self.now, max_per_query=20)
        )

        self.assertEqual([item.id for item in result], ["reply"])
        self.assertTrue(result[0].is_reply)
        self.collector._collect_source_timeline.assert_awaited_once_with(
            "trustedsource",
            self.start,
            self.now,
            limit=200,
            include_replies=True,
        )
        self.collector._run_queries.assert_not_awaited()

    def test_automatic_recovery_is_source_scoped_and_external_hits_do_not_leak(self):
        recovered = self._update("recovered", "trustedsource", "plain source update")
        external = self._update("external", "randomfan", "JEONGHAN update")
        self.collector._collect_source_timeline = AsyncMock(
            side_effect=XCollectionError("timeline failed")
        )
        self.collector._run_queries = AsyncMock(return_value=[external, recovered])

        result = asyncio.run(
            self.collector.collect_window(self.start, self.now, max_per_query=20)
        )

        self.assertEqual([item.id for item in result], ["recovered"])
        self.assertTrue(self.collector.last_errors)
        query = self.collector._run_queries.await_args.args[0][0]
        self.assertIn("from:trustedsource", query)
        self.assertNotIn("JEONGHAN", query)

    def test_keyword_only_window_is_still_source_scoped(self):
        recovered = self._update("recovered", "trustedsource", "plain source update")
        external = self._update("external", "randomfan", "JEONGHAN update")
        self.collector._run_queries = AsyncMock(return_value=[external, recovered])

        result = asyncio.run(
            self.collector.collect_window(
                self.start,
                self.now,
                include_sources=False,
                include_keywords=True,
                max_per_query=20,
            )
        )

        self.assertEqual([item.id for item in result], ["recovered"])
        query = self.collector._run_queries.await_args.args[0][0]
        self.assertIn("from:trustedsource", query)

    def test_manual_archive_search_is_source_only_and_queries_are_author_scoped(self):
        external = self._update("external", "randomfan", "JEONGHAN update")
        trusted = self._update("trusted", "trustedsource", "JEONGHAN update")
        self.collector._run_queries = AsyncMock(return_value=[external, trusted])

        result = asyncio.run(
            self.collector.search_archive(
                ["from:randomfan JEONGHAN"],
                start=self.start,
                end=self.now,
                max_per_query=20,
            )
        )

        self.assertEqual([item.id for item in result], ["trusted"])
        self.assertGreaterEqual(self.collector._run_queries.await_count, 1)
        for call in self.collector._run_queries.await_args_list:
            for query in call.args[0]:
                self.assertIn("from:trustedsource", query)
                self.assertNotIn("from:randomfan", query)

    def test_duplicate_source_recovery_results_are_deduplicated_by_post_id(self):
        recovered = self._update("same-id", "trustedsource", "plain source update")
        duplicate = self._update(
            "same-id",
            "trustedsource",
            "plain source update",
            media_kind="video",
        )
        self.collector._collect_source_timeline = AsyncMock(
            side_effect=XCollectionError("timeline failed")
        )
        self.collector._run_queries = AsyncMock(return_value=[recovered, duplicate])

        result = asyncio.run(
            self.collector.collect_window(self.start, self.now, max_per_query=20)
        )

        self.assertEqual([item.id for item in result], ["same-id"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].media[0].kind, "video")

    def test_source_results_are_deterministically_oldest_to_newest(self):
        newer = self._update("20", "trustedsource", "later", minutes_ago=5)
        older = self._update("10", "trustedsource", "earlier", minutes_ago=30)
        self.collector._collect_source_timeline = AsyncMock(return_value=[newer, older])

        result = asyncio.run(self.collector.collect_window(self.start, self.now))

        self.assertEqual([item.id for item in result], ["10", "20"])

    def test_complete_source_24h_respects_configured_reply_policy(self):
        self.collector.sources[0]["include_replies"] = False
        post = self._update("original", "trustedsource", "plain post")
        self.collector._collect_source_timeline = AsyncMock(return_value=[post])

        result = asyncio.run(
            self.collector.collect_source(
                "trustedsource",
                self.now - timedelta(hours=24),
                self.now,
            )
        )

        self.assertEqual([item.id for item in result], ["original"])
        self.assertFalse(self.collector._collect_source_timeline.await_args.kwargs["include_replies"])

    def test_complete_source_24h_rejects_unconfigured_handle_before_network(self):
        self.collector._collect_source_timeline = AsyncMock()

        with self.assertRaises(XCollectionError):
            asyncio.run(
                self.collector.collect_source(
                    "randomfan",
                    self.now - timedelta(hours=24),
                    self.now,
                )
            )

        self.collector._collect_source_timeline.assert_not_awaited()

    def test_event_recovery_rejects_unconfigured_selected_post(self):
        selected = self._update("outside", "randomfan", "JEONGHAN live")
        self.collector._get_api = AsyncMock()
        self.collector._run_queries = AsyncMock()

        with self.assertRaises(XCollectionError):
            asyncio.run(self.collector.collect_event(selected))

        self.collector._get_api.assert_not_awaited()
        self.collector._run_queries.assert_not_awaited()

    def test_event_recovery_queries_only_selected_configured_author(self):
        selected = self._update("selected", "trustedsource", "JEONGHAN live part 1")
        selected.conversation_id = "synthetic-thread"
        external = self._update("outside", "randomfan", "JEONGHAN live part 2")
        trusted = self._update("trusted", "trustedsource", "JEONGHAN live part 2")
        self.collector._get_api = AsyncMock(return_value=object())
        self.collector._run_queries = AsyncMock(return_value=[external, trusted])

        result = asyncio.run(self.collector.collect_event(selected))

        self.assertEqual({item.author for item in result}, {"trustedsource"})
        for query in self.collector._run_queries.await_args.args[0]:
            self.assertIn("from:trustedsource", query)
            self.assertNotIn("from:randomfan", query)

    def test_runtime_xcollector_reuses_completeness_aware_timeline_method(self):
        self.assertIs(
            XCollector._collect_source_timeline,
            CompleteWindowXCollector._collect_source_timeline,
        )

    def test_runtime_timeline_records_ledger_once_and_install_repairs_binding(self):
        from app import source_ledger_runtime as runtime
        from app.source_ledger import SourceWindowStatus

        original = AsyncMock(return_value=[])
        original._source_ledger_hook = False
        ledger = MagicMock()
        with patch.object(CompleteWindowXCollector, "_collect_source_timeline", original), \
                patch.object(XCollector, "_collect_source_timeline", original), \
                patch.object(runtime, "_ledger_for", return_value=ledger), \
                patch.object(runtime, "_raw_count", return_value=0):
            runtime.install()
            wrapped = CompleteWindowXCollector._collect_source_timeline
            XCollector._collect_source_timeline = original
            runtime.install()
            self.assertIs(XCollector._collect_source_timeline, wrapped)
            self.assertIs(CompleteWindowXCollector._collect_source_timeline, wrapped)
            asyncio.run(self.collector._collect_source_timeline(
                "trustedsource", self.start, self.now, limit=20, include_replies=True,
            ))

        original.assert_awaited_once()
        ledger.start_attempt.assert_called_once()
        ledger.finish.assert_called_once()
        self.assertEqual(ledger.finish.call_args.args[0].status, SourceWindowStatus.COMPLETE)


if __name__ == "__main__":
    unittest.main()
