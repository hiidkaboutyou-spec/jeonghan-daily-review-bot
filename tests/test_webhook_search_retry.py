from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.config import Settings
from app.state import StateStore
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant
from app.webhook_server import WebhookRuntime
from app.x_client import XCollectionError


class WebhookSearchRetryTests(unittest.TestCase):
    def application(self, path):
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.settings = Settings.load(require_secrets=False)
        app.state = StateStore(path)
        app.telegram = SimpleNamespace(is_admin_message=Mock(return_value=True), send_message=Mock())
        app.archive_db = SimpleNamespace(search=Mock(return_value=[]))
        app.writer = SimpleNamespace(expand_search=Mock(return_value=["@haniwadda"]))
        app.collector = SimpleNamespace(
            filter_configured_updates=lambda updates: list(updates),
            search_archive=AsyncMock(side_effect=XCollectionError("upstream unavailable")),
        )
        return app

    def test_real_webhook_search_outage_finishes_once_and_replay_is_silent(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            app = self.application(path)
            runtime = WebhookRuntime()
            self.addCleanup(runtime.executor.shutdown, wait=True, cancel_futures=True)
            runtime.application = app
            item = {"update_id": 50, "message": {"text": "@haniwadda"}}
            with patch.object(runtime, "_save_and_backup_if_changed"):
                self.assertTrue(runtime.process_update_sync(item))
                sent = [call.args[0] for call in app.telegram.send_message.call_args_list]
                self.assertEqual(sum("اول آرشیو" in text for text in sent), 1)
                self.assertTrue(any("X" in text and "دسترسی" in text for text in sent))
                self.assertEqual(app.state.telegram_offset, 51)
                self.assertEqual(StateStore(path).telegram_offset, 51)
                self.assertTrue(runtime.process_update_sync(item))
                self.assertEqual(app.telegram.send_message.call_count, len(sent))
                app.collector.search_archive.assert_awaited_once()

    def test_poison_budget_survives_reopen_and_unblocks_next_update(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            for attempt in range(1, 4):
                app = self.application(path)
                app._process_one_telegram_update = AsyncMock(side_effect=RuntimeError("broken handler"))
                runtime = WebhookRuntime()
                runtime.application = app
                try:
                    with patch.object(runtime, "_save_and_backup_if_changed"):
                        self.assertEqual(runtime.process_update_sync({"update_id": 60}), attempt == 3)
                    app._process_one_telegram_update.assert_awaited_once()
                    reopened = StateStore(path)
                    self.assertEqual(reopened.telegram_offset, 61 if attempt == 3 else 0)
                    self.assertEqual(reopened.telegram_failure_count(60), 0 if attempt == 3 else attempt)
                finally:
                    runtime.executor.shutdown(wait=True, cancel_futures=True)
            app._process_one_telegram_update = AsyncMock()
            with patch.object(runtime, "_save_and_backup_if_changed"):
                self.assertTrue(runtime.process_update_sync({"update_id": 61}))
            self.assertEqual(app.state.telegram_offset, 62)
