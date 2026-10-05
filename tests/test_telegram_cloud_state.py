from __future__ import annotations

import base64
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.telegram_cloud_state import MAX_RESTORABLE_BACKUP_BYTES, backup_to_telegram
from tools.state_backup import BackupError


class TelegramCloudStateTests(unittest.TestCase):
    def test_oversized_backup_is_never_uploaded_or_pinned(self):
        class FakeTelegram:
            token = "test-token"
            review_chat_id = "test-chat"

            def __init__(self):
                self.api_calls = []

            def api(self, *args, **kwargs):
                self.api_calls.append((args, kwargs))
                raise AssertionError("Telegram API must not be called for an unrestorable backup")

        def write_oversized_backup(_state_dir: Path, output: Path) -> None:
            output.write_bytes(b"x" * (MAX_RESTORABLE_BACKUP_BYTES + 1))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state_dir = root / ".state"
            state_dir.mkdir()
            database = state_dir / "private-review.sqlite3"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE sample(value TEXT)")
            key = base64.b64encode(bytes(range(32))).decode("ascii")
            telegram = FakeTelegram()
            with patch.dict(os.environ, {"STATE_BACKUP_KEY": key}):
                with patch("app.telegram_cloud_state.encrypt", side_effect=write_oversized_backup):
                    with self.assertRaisesRegex(BackupError, "too large to restore"):
                        backup_to_telegram(telegram, state_dir)
            self.assertEqual(telegram.api_calls, [])
            self.assertFalse((state_dir / "jeonghan-assistant-state.enc").exists())


if __name__ == "__main__":
    unittest.main()
