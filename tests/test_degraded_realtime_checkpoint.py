from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.state import StateStore
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant


class DegradedRealtimeCheckpointTests(unittest.TestCase):
    def _app(
        self,
        *,
        now: datetime,
        degraded_at: str = "",
        failed: dict[str, str] | None = None,
    ) -> WebhookAwarePersonalAssistant:
        app = object.__new__(WebhookAwarePersonalAssistant)
        collector = SimpleNamespace(
            last_errors=[
                "authenticated_x_degraded: public_syndication_fallback",
                "keyword_search_unavailable: authenticated_x_degraded",
            ],
            provider_preflight_blocked=lambda: False,
            collect_window=AsyncMock(return_value=[]),
            sources=[
                {"handle": "alpha", "enabled": True},
                {"handle": "beta", "enabled": True},
            ],
            _hani_degraded_attempted_sources=["alpha", "beta"],
            _hani_degraded_failed_sources=dict(failed or {}),
        )
        app.collector = collector
        app.settings = SimpleNamespace(
            runtime={
                "scheduled_min_interval_minutes": 12,
                "scheduled_lookback_hours": 24,
            },
            sources=[
                {"handle": "alpha", "enabled": True},
                {"handle": "beta", "enabled": True},
            ],
        )
        app.state = SimpleNamespace(
            data={
                "last_auto_run": (now - timedelta(hours=20)).isoformat(),
                "last_auto_attempt": (now - timedelta(minutes=20)).isoformat(),
                "last_degraded_scan_at": degraded_at,
                "last_x_error_notice": "old",
                "last_failed_sources": ["old"],
                "x_scan_failure_streak": 3,
            },
            is_seen=lambda _update_id: False,
            queue_updates=Mock(),
        )
        app._notify_x_failure_if_due = Mock()
        return app

    def test_full_public_source_pass_advances_only_degraded_checkpoint(self):
        now = datetime.now(timezone.utc)
        app = self._app(now=now)
        authoritative_before = app.state.data["last_auto_run"]

        with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": "degraded"}):
            asyncio.run(app.run_scheduled_scan())

        self.assertEqual(app.state.data["last_auto_run"], authoritative_before)
        self.assertTrue(app.state.data["last_degraded_scan_at"])
        self.assertEqual(app.state.data["x_scan_failure_streak"], 0)
        self.assertEqual(app.state.data["last_failed_sources"], [])
        app._notify_x_failure_if_due.assert_not_called()

    def test_next_degraded_pass_uses_realtime_checkpoint_with_two_hour_overlap(self):
        now = datetime.now(timezone.utc)
        degraded_at = (now - timedelta(minutes=20)).isoformat()
        app = self._app(now=now, degraded_at=degraded_at)

        with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": "degraded"}):
            asyncio.run(app.run_scheduled_scan())

        args = app.collector.collect_window.await_args.args
        start, end = args[0], args[1]
        self.assertLess(end - start, timedelta(hours=3))
        self.assertGreater(end - start, timedelta(hours=2))
        self.assertEqual(app.state.data["last_auto_run"], (now - timedelta(hours=20)).isoformat())

    def test_source_failure_does_not_advance_degraded_checkpoint(self):
        now = datetime.now(timezone.utc)
        previous = (now - timedelta(hours=1)).isoformat()
        app = self._app(now=now, degraded_at=previous, failed={"beta": "timeout"})
        app.collector.last_errors.append("@beta: recovery_chain_failed (timeout)")

        with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": "degraded"}):
            asyncio.run(app.run_scheduled_scan())

        self.assertEqual(app.state.data["last_degraded_scan_at"], previous)
        self.assertEqual(app.state.data["x_scan_failure_streak"], 4)
        app._notify_x_failure_if_due.assert_called_once()

    def test_priority_collector_source_failure_blocks_checkpoint(self):
        now = datetime.now(timezone.utc)
        app = self._app(now=now)
        app.collector.sources.append({"handle": "priority_only", "enabled": True})
        app.collector._hani_degraded_attempted_sources.append("priority_only")
        app.collector._hani_degraded_failed_sources = {"priority_only": "timeout"}
        app.collector.last_errors.append(
            "@priority_only: recovery_chain_failed (timeout)"
        )

        with patch.dict(os.environ, {"X_PROVIDER_PREFLIGHT": "degraded"}):
            asyncio.run(app.run_scheduled_scan())

        self.assertEqual(app.state.data["last_degraded_scan_at"], "")
        self.assertEqual(app.state.data["x_scan_failure_streak"], 4)

    def test_state_store_preserves_degraded_checkpoint_across_restart(self):
        stamp = "2026-10-06T21:00:00+00:00"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(
                json.dumps({"schema": 4, "last_degraded_scan_at": stamp}),
                encoding="utf-8",
            )
            state = StateStore(path)
        self.assertEqual(state.data["last_degraded_scan_at"], stamp)


if __name__ == "__main__":
    unittest.main()
