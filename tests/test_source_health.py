from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.source_health import SourceHealthStore


class SourceHealthTests(unittest.TestCase):
    def test_success_failure_and_persistence_are_sanitized(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.sqlite3"
            first = SourceHealthStore(path)
            first.success("SourceA", 12, 345)
            item = first.get("sourcea")
            self.assertEqual(item.recent_result_count, 12)
            self.assertEqual(item.consecutive_failures, 0)
            self.assertEqual(item.status(), "healthy")
            first.failure("SourceA", "XCollectionError auth_token=secret", 500)
            item = first.get("sourcea")
            self.assertEqual(item.consecutive_failures, 1)
            self.assertNotIn("secret", item.last_error_code)
            self.assertNotIn("=", item.last_error_code)
            first.close()
            second = SourceHealthStore(path)
            self.assertEqual(second.get("sourcea").consecutive_failures, 1)
            second.close()

    def test_three_failures_are_unhealthy_and_success_resets(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SourceHealthStore(Path(temp) / "private.sqlite3")
            for _ in range(3):
                store.failure("x", "TimeoutError", 20)
            self.assertEqual(store.get("x").status(), "unhealthy")
            store.success("x", 0, 10)
            self.assertEqual(store.get("x").status(), "healthy")
            store.close()

    def test_stale_status(self):
        with tempfile.TemporaryDirectory() as temp:
            store = SourceHealthStore(Path(temp) / "private.sqlite3")
            store.success("x", 1, 10)
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            store.conn.execute("UPDATE source_health SET last_success=? WHERE source='x'", (old,))
            store.conn.commit()
            self.assertEqual(store.get("x").status(), "stale")
            store.close()


if __name__ == "__main__":
    unittest.main()
