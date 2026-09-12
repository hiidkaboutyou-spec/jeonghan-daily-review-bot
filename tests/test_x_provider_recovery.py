from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models import Update
from app.x_client import XCollector
from app.x_provider_recovery import (
    _collect_source_with_provider_recovery,
    _collect_window_with_provider_recovery,
    _select_rotating_batch,
    collect_degraded_window,
)


def _update(handle: str, identifier: str) -> Update:
    return Update(
        id=identifier,
        url=f"https://x.com/{handle}/status/{identifier}",
        author=handle,
        author_name=handle,
        text="Jeonghan update",
        created_at=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc),
        raw_query=f"syndication:@{handle}",
    )


def _sources(count: int) -> list[dict]:
    return [
        {
            "handle": f"source{index}",
            "enabled": True,
            "include_replies": True,
            "priority": index,
        }
        for index in range(count)
    ]


class XProviderRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 10, 7, 0, tzinfo=timezone.utc)
        self.end = datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)

    def test_default_fallback_batch_covers_all_configured_sources(self):
        collector = XCollector({}, _sources(31), [])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("X_SYNDICATION_FALLBACK_BATCH_SIZE", None)
            selected, total = _select_rotating_batch(collector)

        self.assertEqual(total, 31)
        self.assertEqual(len(selected), 31)
        self.assertEqual(
            [item["handle"] for item in selected],
            [f"source{index}" for index in range(31)],
        )

    def test_rotates_fallback_batch_across_failure_streaks(self):
        collector = XCollector({}, _sources(10), [])
        collector._phase3_state = SimpleNamespace(data={"x_scan_failure_streak": 1})
        with patch.dict(os.environ, {"X_SYNDICATION_FALLBACK_BATCH_SIZE": "8"}):
            selected, total = _select_rotating_batch(collector)
        self.assertEqual(total, 10)
        self.assertEqual(
            [item["handle"] for item in selected],
            ["source8", "source9", "source0", "source1", "source2", "source3", "source4", "source5"],
        )

    @patch("app.x_provider_recovery.collect_syndication_timeline")
    def test_degraded_window_uses_public_sources_and_marks_partial(self, syndication):
        def result(handle, *_args, **_kwargs):
            index = handle.replace("source", "")
            return SimpleNamespace(updates=[_update(handle, index)], raw_seen=1)

        syndication.side_effect = result
        collector = XCollector({}, _sources(10), [])
        with patch.dict(
            os.environ,
            {
                "X_PROVIDER_PREFLIGHT": "degraded",
                "X_SYNDICATION_FALLBACK_BATCH_SIZE": "8",
            },
        ):
            updates = asyncio.run(
                collect_degraded_window(collector, self.start, self.end)
            )

        self.assertEqual(len(updates), 8)
        self.assertEqual(syndication.call_count, 8)
        self.assertTrue(any("public_syndication_fallback" in item for item in collector.last_errors))
        self.assertTrue(any("8/10" in item for item in collector.last_errors))
        self.assertTrue(any("keyword_search_unavailable" in item for item in collector.last_errors))

    @patch("app.x_provider_recovery.collect_syndication_timeline")
    @patch("app.x_provider_recovery._ORIGINAL_COLLECT_WINDOW", new_callable=AsyncMock)
    def test_degraded_wrapper_bypasses_authenticated_collector(self, original, syndication):
        syndication.return_value = SimpleNamespace(
            updates=[_update("source0", "1")],
            raw_seen=1,
        )
        collector = XCollector({}, _sources(1), [])
        with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": "degraded"}):
            updates = asyncio.run(
                _collect_window_with_provider_recovery(
                    collector,
                    self.start,
                    self.end,
                    include_keywords=False,
                )
            )
        original.assert_not_awaited()
        self.assertEqual([item.id for item in updates], ["1"])

    @patch("app.x_provider_recovery.collect_syndication_timeline")
    def test_manual_source_works_during_degraded_provider(self, syndication):
        syndication.return_value = SimpleNamespace(
            updates=[_update("source0", "2")],
            raw_seen=1,
        )
        collector = XCollector({}, _sources(1), [])
        with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": "degraded"}):
            updates = asyncio.run(
                _collect_source_with_provider_recovery(
                    collector,
                    "source0",
                    self.start,
                    self.end,
                )
            )
        self.assertEqual([item.id for item in updates], ["2"])
        self.assertTrue(any("public_syndication_fallback" in item for item in collector.last_errors))


if __name__ == "__main__":
    unittest.main()
