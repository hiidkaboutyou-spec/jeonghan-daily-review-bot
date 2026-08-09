from __future__ import annotations

import base64
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.state_backup import BackupError, encrypt, restore


class StateBackupTests(unittest.TestCase):
    def key(self) -> str:
        return base64.b64encode(bytes(range(32))).decode("ascii")

    def make_state(self, root: Path) -> tuple[bytes, bytes]:
        state_dir = root / ".state"
        state_dir.mkdir()
        state_bytes = json.dumps({"schema_version": 3, "telegram_offset": 17}).encode()
        (state_dir / "state.json").write_bytes(state_bytes)
        db = state_dir / "private-review.sqlite3"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample(value) VALUES ('private')")
        conn.commit()
        conn.close()
        return state_bytes, db.read_bytes()

    def test_roundtrip_restores_valid_json_and_sqlite(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            state_bytes, db_bytes = self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            self.assertNotIn(b"private", encrypted.read_bytes())
            restore_dir = root / "restore"
            restored = restore(encrypted, restore_dir)
            self.assertEqual(set(restored), {"state.json", "private-review.sqlite3"})
            self.assertEqual((restore_dir / "state.json").read_bytes(), state_bytes)
            self.assertEqual((restore_dir / "private-review.sqlite3").read_bytes(), db_bytes)

    def test_wrong_key_fails_authentication_without_mutating_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_state(root)
            encrypted = root / "backup.enc"
            with patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
                encrypt(root / ".state", encrypted)
            destination = root / "restore"
            destination.mkdir()
            sentinel = destination / "state.json"
            sentinel.write_text('{"keep": true}', encoding="utf-8")
            wrong = base64.b64encode(b"x" * 32).decode("ascii")
            with patch.dict(os.environ, {"STATE_BACKUP_KEY": wrong}):
                with self.assertRaises(BackupError):
                    restore(encrypted, destination, only_missing=False)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"keep": true}')
            self.assertFalse((destination / "private-review.sqlite3").exists())

    def test_only_missing_never_overwrites_valid_cache_state(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            destination = root / "restore"
            destination.mkdir()
            existing = destination / "state.json"
            existing.write_text('{"cache": "newer"}', encoding="utf-8")
            restored = restore(encrypted, destination, only_missing=True)
            self.assertEqual(existing.read_text(encoding="utf-8"), '{"cache": "newer"}')
            self.assertEqual(restored, ["private-review.sqlite3"])

    def test_missing_or_malformed_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_state(root)
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(BackupError):
                    encrypt(root / ".state", root / "backup.enc")
            with patch.dict(os.environ, {"STATE_BACKUP_KEY": base64.b64encode(b"short").decode()}):
                with self.assertRaises(BackupError):
                    encrypt(root / ".state", root / "backup.enc")

    def test_corrupt_sqlite_is_rejected_before_encryption(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            state = root / ".state"
            state.mkdir()
            (state / "state.json").write_text("{}", encoding="utf-8")
            (state / "private-review.sqlite3").write_bytes(b"not sqlite")
            with self.assertRaises(BackupError):
                encrypt(state, root / "backup.enc")


if __name__ == "__main__":
    unittest.main()
