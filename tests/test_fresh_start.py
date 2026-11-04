from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models import Update
from app.state import StateStore
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant


def _update(update_id: str, created_at: datetime) -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/jeonghan/status/{update_id}",
        author="jeonghan",
        author_name="Jeonghan",
        text="new post",
        created_at=created_at,
    )


class FreshStartTests(unittest.TestCase):
    def test_fresh_start_clears_only_pending_delivery_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.json")
            old_a = _update("old-a", datetime(2026, 11, 3, tzinfo=timezone.utc))
            old_b = _update("old-b", datetime(2026, 11, 3, 1, tzinfo=timezone.utc))
            store.queue_updates([old_a, old_b])

            started = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
            self.assertEqual(store.apply_fresh_start("campaign:fresh-start", started), 2)
            self.assertEqual(store.data["pending_delivery"], [])
            self.assertTrue(store.is_seen("old-a"))
            self.assertTrue(store.is_seen("old-b"))
            self.assertEqual(store.data["fresh_start_since"], started.isoformat())
            self.assertEqual(store.data["last_auto_run"], started.isoformat())

            new_item = _update("new", started + timedelta(minutes=1))
            store.queue_updates([new_item])
            self.assertEqual(store.apply_fresh_start("campaign:fresh-start", started), 0)
            self.assertEqual(
                [item["id"] for item in store.data["pending_delivery"]],
                ["new"],
            )

            store.save()
            restored = StateStore(Path(temp) / "state.json")
            self.assertEqual(restored.data["fresh_start_run_id"], "campaign:fresh-start")
            self.assertIn("campaign:fresh-start", restored.data["fresh_start_tokens"])
            self.assertEqual(restored.data["fresh_start_since"], started.isoformat())
            self.assertEqual([item["id"] for item in restored.data["pending_delivery"]], ["new"])

    def test_fresh_start_requires_run_id(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StateStore(Path(temp) / "state.json")
            with self.assertRaises(ValueError):
                store.apply_fresh_start("")


class FreshStartProductionScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_concrete_scheduled_scan_excludes_posts_before_fresh_start_floor(self):
        with tempfile.TemporaryDirectory() as temp:
            now = datetime.now(timezone.utc)
            floor = now - timedelta(minutes=45)
            state = StateStore(Path(temp) / "state.json")
            state.data["last_auto_run"] = (now - timedelta(hours=3)).isoformat()
            state.data["last_auto_attempt"] = (now - timedelta(hours=1)).isoformat()
            state.data["fresh_start_since"] = floor.isoformat()

            old = _update("before-floor", floor - timedelta(seconds=1))
            new = _update("after-floor", floor + timedelta(seconds=1))
            sources = [{"handle": "jeonghan", "enabled": True}]
            collector = SimpleNamespace(
                sources=sources,
                last_errors=[],
                provider_preflight_blocked=lambda: False,
                collect_window=AsyncMock(return_value=[old, new]),
            )
            app = object.__new__(WebhookAwarePersonalAssistant)
            app.state = state
            app.collector = collector
            app.settings = SimpleNamespace(
                sources=sources,
                runtime={
                    "scheduled_min_interval_minutes": 1,
                    "scheduled_lookback_hours": 24,
                },
            )

            with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": ""}):
                await app.run_scheduled_scan()

            collector.collect_window.assert_awaited_once()
            start = collector.collect_window.await_args.args[0]
            self.assertEqual(start, floor)
            self.assertEqual(
                [item["id"] for item in state.data["pending_delivery"]],
                ["after-floor"],
            )
            self.assertEqual(state.data["fresh_start_since"], "")
            self.assertGreaterEqual(
                datetime.fromisoformat(state.data["last_auto_run"]),
                floor,
            )


if __name__ == "__main__":
    unittest.main()
