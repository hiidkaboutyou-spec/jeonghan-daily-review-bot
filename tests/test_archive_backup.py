from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.archive_backup import export_archive_jsonl, import_archive_jsonl
from app.archive_store import ArchiveStore
from app.config import ROOT
from app.models import Update


class ArchiveBackupTests(unittest.TestCase):
    def _update(self, id_: str, text: str) -> Update:
        return Update(
            id=id_,
            url=f"https://x.com/source/status/{id_}",
            author="source",
            author_name="Source",
            text=text,
            created_at=datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
            conversation_id=id_,
        )

    def test_export_import_roundtrip_keeps_archive_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "private-review.sqlite3"
            backup = root / "archive.jsonl"
            store = ArchiveStore(db)
            store.index_update(self._update("1", "JEONGHAN live"), translated_text="ترجمه", caption="کپشن")
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE secret_test(value TEXT)")
                conn.execute("INSERT INTO secret_test VALUES(?)", ("auth_token=DO_NOT_EXPORT",))
                conn.commit()
            store.close()

            self.assertEqual(export_archive_jsonl(db, backup), 1)
            exported = backup.read_text(encoding="utf-8")
            self.assertNotIn("DO_NOT_EXPORT", exported)
            self.assertNotIn("secret_test", exported)

            restored_db = root / "restored.sqlite3"
            restored = ArchiveStore(restored_db)
            imported, skipped = import_archive_jsonl(restored, backup)
            self.assertEqual((imported, skipped), (1, 0))
            results = restored.search("JEONGHAN")
            self.assertEqual([item.id for item in results], ["1"])
            self.assertEqual(restored.count(), 1)
            restored.close()

    def test_import_skips_malformed_rows_without_deleting_valid_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "private-review.sqlite3"
            backup = root / "archive.jsonl"
            source = ArchiveStore(db)
            source.index_update(self._update("1", "first"))
            source.close()
            export_archive_jsonl(db, backup)
            with backup.open("a", encoding="utf-8") as handle:
                handle.write("{not-json}\n")
                handle.write('{"update":"wrong"}\n')

            target = ArchiveStore(root / "target.sqlite3")
            target.index_update(self._update("existing", "existing"))
            imported, skipped = import_archive_jsonl(target, backup)
            self.assertEqual(imported, 1)
            self.assertEqual(skipped, 2)
            self.assertEqual(target.count(), 2)
            target.close()

    def test_workflow_persists_only_explicit_safe_private_database(self):
        text = (ROOT / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
        self.assertIn(".state/private-review.sqlite3", text)
        self.assertIn("jeonghan-private-review-v1-", text)
        self.assertNotIn(".state/x_accounts.db", "\n".join(
            line for line in text.splitlines() if line.strip().startswith("path:")
        ))
        self.assertNotIn("path: .state\n", text)


if __name__ == "__main__":
    unittest.main()
