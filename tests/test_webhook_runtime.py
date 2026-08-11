from __future__ import annotations

import os
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.telegram import TelegramTransientError
from app.telegram_cloud_state import backup_fingerprint, ensure_process_backup_key
from app.webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant, github_actions_polling_only
from app.webhook_server import WebhookRuntime


class _FakeState:
    def __init__(self) -> None:
        self.telegram_offset = 0
        self.saved = 0

    def clear_telegram_failure(self, _update_id: int) -> None:
        return None

    def save(self) -> None:
        self.saved += 1


class _FakeApp:
    def __init__(self, *, transient: bool = False) -> None:
        self.state = _FakeState()
        self.transient = transient
        self.calls = 0

    async def _process_one_telegram_update(self, _item) -> None:
        self.calls += 1
        if self.transient:
            raise TelegramTransientError("temporary")


class WebhookRuntimeTests(unittest.TestCase):
    def test_github_actions_only_mode_is_explicit(self):
        with patch.dict(os.environ, {"ASSISTANT_RUNTIME_MODE": "github_actions_polling"}, clear=False):
            self.assertTrue(github_actions_polling_only())
        with patch.dict(os.environ, {"ASSISTANT_RUNTIME_MODE": ""}, clear=False):
            self.assertFalse(github_actions_polling_only())

    def test_partial_x_attempt_is_persistently_throttled_between_action_passes(self):
        now = datetime.now(timezone.utc)
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.state = SimpleNamespace(
            data={
                "last_auto_run": (now - timedelta(hours=1)).isoformat(),
                "last_auto_attempt": (now - timedelta(minutes=5)).isoformat(),
            }
        )
        app.settings = SimpleNamespace(
            runtime={"scheduled_min_interval_minutes": 12, "scheduled_lookback_hours": 24}
        )
        app.collector = SimpleNamespace(collect_window=AsyncMock())

        asyncio.run(app.run_scheduled_scan())

        app.collector.collect_window.assert_not_awaited()

    def test_x_warning_requires_three_consecutive_failed_scans(self):
        now = datetime.now(timezone.utc)
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.state = SimpleNamespace(data={})
        app._notify_x_failure_if_due = Mock()

        app._record_x_scan_failure(now)
        app._record_x_scan_failure(now)
        app._notify_x_failure_if_due.assert_not_called()

        app._record_x_scan_failure(now)
        app._notify_x_failure_if_due.assert_called_once_with(now)
        self.assertEqual(app.state.data["x_scan_failure_streak"], 3)

    def test_runtime_secret_is_stable_and_telegram_compatible(self):
        first = derive_runtime_secret("123:abc")
        second = derive_runtime_secret("123:abc")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertRegex(first, r"^[a-f0-9]+$")

    def test_maintenance_url_uses_same_webhook_origin(self):
        self.assertEqual(
            maintenance_url_from_webhook("https://assistant.example/telegram/webhook"),
            "https://assistant.example/maintenance",
        )
        self.assertEqual(maintenance_url_from_webhook("not-a-url"), "")

    def test_telegram_token_can_supply_process_only_backup_key(self):
        with patch.dict(os.environ, {"STATE_BACKUP_KEY": ""}, clear=False):
            ensure_process_backup_key("123:abc")
            value = os.environ.get("STATE_BACKUP_KEY", "")
            self.assertTrue(value)
            self.assertNotIn("123:abc", value)

    def test_existing_dedicated_backup_key_is_preserved(self):
        existing = "ZmFrZS1iYXNlNjQta2V5LWZvci10ZXN0aW5nLW9ubHk="
        with patch.dict(os.environ, {"STATE_BACKUP_KEY": existing}, clear=False):
            ensure_process_backup_key("123:abc")
            self.assertEqual(os.environ["STATE_BACKUP_KEY"], existing)

    def test_backup_fingerprint_changes_when_sqlite_wal_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            state_dir = Path(temp)
            (state_dir / "state.json").write_text("{}", encoding="utf-8")
            (state_dir / "private-review.sqlite3").write_bytes(b"db")
            before = backup_fingerprint(state_dir)
            (state_dir / "private-review.sqlite3-wal").write_bytes(b"new durable rows")
            after = backup_fingerprint(state_dir)
            self.assertNotEqual(before, after)

    def test_render_external_url_is_used_without_manual_public_url(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_BASE_URL": "", "RENDER_EXTERNAL_URL": "https://assistant.onrender.com"},
            clear=False,
        ):
            self.assertEqual(
                WebhookRuntime._public_url_from_environment(),
                "https://assistant.onrender.com",
            )

    def test_successful_update_is_persisted_before_ack(self):
        runtime = WebhookRuntime()
        fake = _FakeApp()
        runtime.application = fake  # type: ignore[assignment]
        with patch.object(runtime, "_save_and_backup_if_changed"):
            handled = runtime.process_update_sync({"update_id": 5})
        self.assertTrue(handled)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(fake.state.telegram_offset, 6)
        self.assertGreaterEqual(fake.state.saved, 1)
        runtime.executor.shutdown(wait=True, cancel_futures=True)

    def test_exhausted_transient_failure_is_not_acknowledged(self):
        runtime = WebhookRuntime()
        fake = _FakeApp(transient=True)
        runtime.application = fake  # type: ignore[assignment]
        with patch.object(runtime, "_save_and_backup_if_changed"):
            handled = runtime.process_update_sync({"update_id": 5})
        self.assertFalse(handled)
        self.assertEqual(fake.calls, 3)
        self.assertEqual(fake.state.telegram_offset, 0)
        runtime.executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
