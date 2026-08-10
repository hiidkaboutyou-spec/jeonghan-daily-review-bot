from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RecoveryWorkflowTests(unittest.TestCase):
    def _text(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_main_tries_artifacts_newest_to_oldest_and_validates_before_restore(self):
        text = self._text("main.yml")
        self.assertIn("sort_by(.created_at) | reverse | .[].id", text)
        self.assertIn("for artifact_id in", text)
        self.assertIn("tools.state_backup validate", text)
        self.assertIn("trying older backup", text)
        self.assertIn("refusing to run with potentially lost private state", text)
        self.assertNotIn("reverse | .[0].id", text)
        self.assertIn("--require state.json private-review.sqlite3", text)

    def test_nightly_uses_same_private_db_cache_and_runtime_concurrency(self):
        main = self._text("main.yml")
        fic = self._text("fic-digest.yml")
        self.assertIn("jeonghan-daily-review-bot-", main)
        self.assertIn("'runtime'", main)
        self.assertIn("jeonghan-daily-review-bot-runtime", fic)
        self.assertIn("path: .state/private-review.sqlite3", main)
        self.assertIn("path: .state/private-review.sqlite3", fic)
        self.assertIn("tools.state_backup validate", fic)
        self.assertIn("--require private-review.sqlite3", fic)
        self.assertNotIn("--require state.json private-review.sqlite3", fic)
        self.assertNotIn("reverse | .[0].id", fic)

    def test_missing_recovery_secret_is_explicit_but_not_fatal(self):
        for name in ("main.yml", "fic-digest.yml"):
            text = self._text(name)
            self.assertIn("Report encrypted recovery disabled", text)
            self.assertIn("STATE_BACKUP_KEY is not configured", text)
            self.assertIn("env.STATE_BACKUP_KEY == ''", text)

    def test_only_ciphertext_artifact_is_uploaded(self):
        for name in ("main.yml", "fic-digest.yml"):
            text = self._text(name)
            self.assertIn("path: .state/private-state-backup.enc", text)
            self.assertNotIn("path: .state/private-review.sqlite3\n          if-no-files-found", text)
            self.assertNotIn("path: .state/state.json\n          if-no-files-found", text)


if __name__ == "__main__":
    unittest.main()
