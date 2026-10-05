from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tools.state_backup import BackupError, LEGACY_FORMAT, encrypt, restore, validate


class StateBackupTests(unittest.TestCase):
    def key(self) -> str:
        return base64.b64encode(bytes(range(32))).decode("ascii")

    def make_state(self, root: Path) -> tuple[bytes, bytes]:
        state_dir = root / ".state"
        state_dir.mkdir()
        state_bytes = json.dumps({"schema_version": 3, "telegram_offset": 17, "marker": "TOP_SECRET_STATE_VALUE"}).encode()
        (state_dir / "state.json").write_bytes(state_bytes)
        db = state_dir / "private-review.sqlite3"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE sample(value TEXT)")
        conn.execute("INSERT INTO sample(value) VALUES ('TOP_SECRET_SQLITE_VALUE')")
        conn.commit()
        conn.close()
        return state_bytes, db.read_bytes()

    def envelope(self, path: Path) -> dict[str, str]:
        return json.loads(path.read_text(encoding="utf-8"))

    def test_roundtrip_restores_valid_json_and_sqlite(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            state_bytes, db_bytes = self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            raw = encrypted.read_bytes()
            self.assertNotIn(b"TOP_SECRET_STATE_VALUE", raw)
            self.assertNotIn(b"TOP_SECRET_SQLITE_VALUE", raw)
            self.assertEqual(validate(encrypted), ["private-review.sqlite3", "state.json"])
            restore_dir = root / "restore"
            restored = restore(encrypted, restore_dir)
            self.assertEqual(set(restored), {"state.json", "private-review.sqlite3"})
            self.assertEqual((restore_dir / "state.json").read_bytes(), state_bytes)
            self.assertEqual((restore_dir / "private-review.sqlite3").read_bytes(), db_bytes)

    def test_fresh_nonce_is_generated_for_every_backup(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            first, second = root / "one.enc", root / "two.enc"
            encrypt(root / ".state", first)
            encrypt(root / ".state", second)
            self.assertNotEqual(self.envelope(first)["nonce"], self.envelope(second)["nonce"])
            self.assertNotEqual(self.envelope(first)["ciphertext"], self.envelope(second)["ciphertext"])

    def test_new_backup_uses_compressed_v2_envelope(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            envelope = self.envelope(encrypted)
            self.assertEqual(envelope["format"], "jeonghan-private-state-backup-v2")
            self.assertEqual(envelope["compression"], "zlib")

    def test_v2_compression_avoids_base64_size_amplification(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            state_dir = root / ".state"
            with sqlite3.connect(state_dir / "private-review.sqlite3") as conn:
                conn.execute("CREATE TABLE repeated(value BLOB)")
                conn.execute("INSERT INTO repeated(value) VALUES (?)", (b"repeatable-state" * 200_000,))
            raw_size = sum((state_dir / name).stat().st_size for name in ("state.json", "private-review.sqlite3"))
            encrypted = root / "backup.enc"
            encrypt(state_dir, encrypted)
            self.assertLess(encrypted.stat().st_size, raw_size)

    def test_legacy_v1_backup_remains_restorable(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            state_bytes, db_bytes = self.make_state(root)
            files = {}
            for name in ("state.json", "private-review.sqlite3"):
                data = (root / ".state" / name).read_bytes()
                files[name] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "data": base64.b64encode(data).decode("ascii"),
                }
            payload = json.dumps(
                {"format": LEGACY_FORMAT, "files": files}, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            nonce = bytes(range(12))
            key = base64.b64decode(self.key())
            envelope = {
                "format": LEGACY_FORMAT,
                "algorithm": "AES-256-GCM",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(
                    AESGCM(key).encrypt(nonce, payload, LEGACY_FORMAT.encode("utf-8"))
                ).decode("ascii"),
            }
            encrypted = root / "legacy.enc"
            encrypted.write_text(json.dumps(envelope), encoding="utf-8")
            destination = root / "restore"
            restore(encrypted, destination)
            self.assertEqual((destination / "state.json").read_bytes(), state_bytes)
            self.assertEqual((destination / "private-review.sqlite3").read_bytes(), db_bytes)

    def test_validation_rejects_authenticated_but_incomplete_backup(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            state = root / ".state"
            state.mkdir()
            db = state / "private-review.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE sample(value TEXT)")
            encrypted = root / "fic-only.enc"
            encrypt(state, encrypted)
            self.assertEqual(
                validate(encrypted, required=("private-review.sqlite3",)),
                ["private-review.sqlite3"],
            )
            with self.assertRaises(BackupError):
                validate(encrypted, required=("state.json", "private-review.sqlite3"))

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

    def test_missing_malformed_and_wrong_length_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_state(root)
            with patch.dict(os.environ, {}, clear=True), self.assertRaises(BackupError):
                encrypt(root / ".state", root / "backup.enc")
            with patch.dict(os.environ, {"STATE_BACKUP_KEY": "not-base64!"}), self.assertRaises(BackupError):
                encrypt(root / ".state", root / "backup.enc")
            with patch.dict(os.environ, {"STATE_BACKUP_KEY": base64.b64encode(b"short").decode()}), self.assertRaises(BackupError):
                encrypt(root / ".state", root / "backup.enc")

    def _assert_tamper_rejected(self, mutate) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            env = self.envelope(encrypted)
            mutate(env)
            encrypted.write_text(json.dumps(env), encoding="utf-8")
            destination = root / "restore"
            destination.mkdir()
            sentinel = destination / "state.json"
            sentinel.write_text('{"keep": true}', encoding="utf-8")
            with self.assertRaises(BackupError):
                restore(encrypted, destination, only_missing=False)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"keep": true}')

    def test_corrupted_ciphertext_is_rejected(self):
        def mutate(env):
            raw = bytearray(base64.b64decode(env["ciphertext"]))
            raw[len(raw) // 2] ^= 1
            env["ciphertext"] = base64.b64encode(bytes(raw)).decode()
        self._assert_tamper_rejected(mutate)

    def test_truncated_backup_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            encrypted.write_bytes(encrypted.read_bytes()[:40])
            with self.assertRaises(BackupError):
                restore(encrypted, root / "restore")

    def test_modified_authentication_tag_is_rejected(self):
        def mutate(env):
            raw = bytearray(base64.b64decode(env["ciphertext"]))
            raw[-1] ^= 1
            env["ciphertext"] = base64.b64encode(bytes(raw)).decode()
        self._assert_tamper_rejected(mutate)

    def test_invalid_json_is_rejected_before_encryption(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            state = Path(temp) / ".state"
            state.mkdir()
            (state / "state.json").write_text("not-json", encoding="utf-8")
            with self.assertRaises(BackupError):
                encrypt(state, Path(temp) / "backup.enc")

    def test_corrupt_sqlite_is_rejected_before_encryption(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            state = Path(temp) / ".state"
            state.mkdir()
            (state / "state.json").write_text("{}", encoding="utf-8")
            (state / "private-review.sqlite3").write_bytes(b"not sqlite")
            with self.assertRaises(BackupError):
                encrypt(state, Path(temp) / "backup.enc")

    def test_missing_source_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            with self.assertRaises(BackupError):
                encrypt(Path(temp), Path(temp) / "backup.enc")

    def test_failed_second_replace_rolls_back_first_replacement(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"STATE_BACKUP_KEY": self.key()}):
            root = Path(temp)
            self.make_state(root)
            encrypted = root / "backup.enc"
            encrypt(root / ".state", encrypted)
            destination = root / "restore"
            destination.mkdir()
            original_state = b'{"keep": "state"}'
            (destination / "state.json").write_bytes(original_state)
            original_db = destination / "private-review.sqlite3"
            conn = sqlite3.connect(original_db)
            conn.execute("CREATE TABLE keep(value TEXT)")
            conn.commit()
            conn.close()
            original_db_bytes = original_db.read_bytes()
            real_replace = os.replace
            calls = {"n": 0}

            def flaky(src, dst):
                if str(src).endswith(".restore.tmp"):
                    calls["n"] += 1
                    if calls["n"] == 2:
                        raise OSError("simulated replace failure")
                return real_replace(src, dst)

            with patch("tools.state_backup.os.replace", side_effect=flaky):
                with self.assertRaises(BackupError):
                    restore(encrypted, destination, only_missing=False)
            self.assertEqual((destination / "state.json").read_bytes(), original_state)
            self.assertEqual(original_db.read_bytes(), original_db_bytes)
            self.assertFalse(any(destination.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
