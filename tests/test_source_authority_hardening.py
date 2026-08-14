from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

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

    def test_manual_archive_search_is_also_source_only(self):
        configured = self._update("configured", "trustedsource", "plain source post")
        external = self._update("external", "randomfan", "JEONGHAN update")
        self.collector._run_queries = AsyncMock(return_value=[external, configured])

        result = asyncio.run(
            self.collector.search_archive(
                ["JEONGHAN"],
                start=self.start,
                end=self.now,
                max_per_query=20,
            )
        )

        self.assertEqual([item.id for item in result], ["configured"])

    def test_unconfigured_source24_is_rejected_before_timeline_lookup(self):
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

    def test_external_selected_event_is_rejected_before_reconstruction(self):
        selected = self._update("external", "randomfan", "JEONGHAN live")
        self.collector._get_api = AsyncMock()

        with self.assertRaises(XCollectionError):
            asyncio.run(self.collector.collect_event(selected))

        self.collector._get_api.assert_not_awaited()

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

    def test_runtime_xcollector_reuses_completeness_aware_timeline_method(self):
        self.assertIs(
            XCollector._collect_source_timeline,
            CompleteWindowXCollector._collect_source_timeline,
        )


if __name__ == "__main__":
    unittest.main()
