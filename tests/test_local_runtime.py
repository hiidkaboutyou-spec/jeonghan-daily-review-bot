from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.local_runtime import run_once


class LocalRuntimeTests(unittest.TestCase):
    def test_keychain_loader_requires_production_values_without_logging_them(self):
        values = {name: f"value-{name}" for name in run_once.REQUIRED_ACCOUNTS}
        with patch.object(run_once, "_read_keychain", side_effect=lambda name, required: values.get(name, "")):
            with patch.dict(os.environ, {}, clear=True):
                run_once.load_keychain_environment()
                self.assertEqual(os.environ["X_COOKIE"], "value-X_COOKIE")
                self.assertEqual(os.environ["ASSISTANT_RUNTIME_MODE"], "github_actions_polling")
                self.assertNotIn("STATE_BACKUP_KEY", os.environ)

    def test_checkout_requires_clean_main_at_origin_main(self):
        answers = {
            ("rev-parse", "--show-toplevel"): str(run_once.ROOT),
            ("status", "--porcelain", "--untracked-files=no"): "",
            ("symbolic-ref", "--short", "HEAD"): "main",
            ("rev-parse", "HEAD"): "abc",
            ("rev-parse", "origin/main"): "abc",
        }
        with patch.object(run_once, "_git", side_effect=lambda *args: answers[args]):
            run_once.verify_checkout()

    def test_state_validation_rejects_corrupt_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text("not-json", encoding="utf-8")
            settings = type("Settings", (), {"state_path": path})()
            with self.assertRaisesRegex(run_once.LocalRuntimeError, "state.json"):
                run_once.validate_state(settings)

    def test_lock_refuses_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "runtime.lock"
            with patch.object(run_once, "LOCK_PATH", lock):
                with run_once.single_writer_lock():
                    with self.assertRaisesRegex(run_once.LocalRuntimeError, "already running"):
                        with run_once.single_writer_lock():
                            pass


if __name__ == "__main__":
    unittest.main()
