from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.telegram_cloud_state import backup_fingerprint, ensure_process_backup_key
from app.webhook_runtime_utils import derive_runtime_secret, maintenance_url_from_webhook


class WebhookRuntimeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
