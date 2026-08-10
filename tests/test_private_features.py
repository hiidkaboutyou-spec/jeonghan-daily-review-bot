from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from app.archive_store import ArchiveStore
from app.models import Update


class ArchiveStoreTests(unittest.TestCase):
    def make_update(self, id_: str, text: str = "JEONGHAN weverse live") -> Update:
        return Update(
            id=id_,
            url=f"https://x.com/source/status/{id_}",
            author="source",
            author_name="Source",
            text=text,
            created_at=datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc),
            conversation_id="live-1",
            category="live",
            event_key="conversation:live-1",
            event_title="لایو جونگهان",
            raw_query="timeline:@source",
        )

    def test_initialization_and_search(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArchiveStore(Path(temp) / "archive.sqlite3")
            store.index_update(self.make_update("1", "JEONGHAN played a game during Weverse live"))
            result = store.search("played game")
            self.assertEqual([item.id for item in result], ["1"])
            store.close()

    def test_restart_keeps_index(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.sqlite3"
            first = ArchiveStore(path)
            first.index_update(self.make_update("2", "airport photos"))
            first.close()
            second = ArchiveStore(path)
            self.assertEqual([item.id for item in second.search("airport")], ["2"])
            second.close()

    def test_duplicate_indexing_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArchiveStore(Path(temp) / "archive.sqlite3")
            update = self.make_update("3")
            store.index_update(update)
            store.index_update(update)
            self.assertEqual(store.count(), 1)
            self.assertEqual(len(store.search("JEONGHAN")), 1)
            store.close()

    def test_rebuild_from_json_and_malformed_records(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArchiveStore(Path(temp) / "archive.sqlite3")
            good = self.make_update("4", "magazine interview")
            count = store.rebuild({"4": good.to_dict(), "bad": "not-a-dict", "broken": {"id": "x"}})
            self.assertEqual(count, 1)
            self.assertEqual([item.id for item in store.search("magazine")], ["4"])
            store.close()

    def test_corrupt_database_is_recovered_and_can_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.sqlite3"
            path.write_bytes(b"not sqlite")
            store = ArchiveStore(path)
            update = self.make_update("5", "brand campaign")
            self.assertEqual(store.rebuild({"5": update.to_dict()}), 1)
            self.assertEqual([item.id for item in store.search("campaign")], ["5"])
            store.close()

    def test_non_ok_quick_check_result_triggers_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "archive.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute("CREATE TABLE old_data(value TEXT)")

            fake = Mock()
            fake.execute.side_effect = [Mock(), Mock(), Mock(fetchone=Mock(return_value=("malformed",)))]
            original_connect = sqlite3.connect
            calls = 0

            def connect(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return fake
                return original_connect(*args, **kwargs)

            with patch("app.archive_store.sqlite3.connect", side_effect=connect):
                store = ArchiveStore(path)
                store.index_update(self.make_update("recovered", "healthy archive"))
                self.assertEqual([item.id for item in store.search("healthy")], ["recovered"])
                store.close()

            self.assertTrue(list(Path(temp).glob("archive.broken-*.sqlite3")))
            fake.close.assert_called_once()

    def test_date_only_search_works_without_fts_terms(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArchiveStore(Path(temp) / "archive.sqlite3")
            store.index_update(self.make_update("6"))
            start = datetime(2026, 7, 14, tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            self.assertEqual([item.id for item in store.search("", start=start, end=end)], ["6"])
            store.close()

    def test_caption_is_searchable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ArchiveStore(Path(temp) / "archive.sqlite3")
            store.index_update(self.make_update("7", "raw text"), caption="جونگهان امروز خیلی بامزه بود")
            self.assertEqual([item.id for item in store.search("بامزه")], ["7"])
            store.close()


if __name__ == "__main__":
    unittest.main()
