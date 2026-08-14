from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.models import MediaItem, Update
from app.x_client import XCollector
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

    def _update(self, id_: str, author: str, text: str, *, video: bool = False) -> Update:
        media = []
        if video:
            media = [
                MediaItem(
                    kind="video",
                    url=f"https://video.example/{id_}.mp4",
                    content_type="video/mp4",
                )
            ]
        return Update(
            id=id_,
            url=f"https://x.com/{author}/status/{id_}",
            author=author,
            author_name=author,
            text=text,
            created_at=self.now - timedelta(minutes=10),
            media=media,
        )

    def test_configured_source_video_survives_without_jeonghan_keyword(self):
        update = self._update("video", "trustedsource", "new clip ✨", video=True)
        kept = self.collector._filter_relevant([update])
        self.assertEqual([item.id for item in kept], ["video"])

    def test_configured_source_post_survives_even_if_caption_names_another_member(self):
        update = self._update("other-member", "trustedsource", "MINGYU new clip", video=True)
        kept = self.collector._filter_relevant([update])
        self.assertEqual([item.id for item in kept], ["other-member"])

    def test_automatic_window_emits_only_configured_sources(self):
        configured = self._update("source-video", "trustedsource", "new clip", video=True)
        external = self._update("external", "randomfan", "JEONGHAN update", video=True)
        self.collector._collect_source_timeline = AsyncMock(return_value=[configured])
        self.collector._run_queries = AsyncMock(return_value=[external])

        result = asyncio.run(self.collector.collect_window(self.start, self.now, max_per_query=20))

        self.assertEqual([item.id for item in result], ["source-video"])
        self.collector._run_queries.assert_awaited_once()

    def test_manual_archive_search_still_allows_relevant_external_discovery(self):
        external = self._update("external", "randomfan", "JEONGHAN update", video=True)
        self.collector._run_queries = AsyncMock(return_value=[external])

        result = asyncio.run(
            self.collector.search_archive(
                ["JEONGHAN"],
                start=self.start,
                end=self.now,
                max_per_query=20,
            )
        )

        self.assertEqual([item.id for item in result], ["external"])

    def test_runtime_xcollector_uses_existing_completeness_methods(self):
        self.assertIs(
            XCollector._collect_source_timeline,
            CompleteWindowXCollector._collect_source_timeline,
        )
        self.assertIs(XCollector.collect_source, CompleteWindowXCollector.collect_source)


if __name__ == "__main__":
    unittest.main()
