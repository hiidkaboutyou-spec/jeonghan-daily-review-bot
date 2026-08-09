from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.models import Update
from app.organizer import organize_updates


class OrderingEdgeTests(unittest.TestCase):
    def make(self, id_: str, text: str, *, conversation: str = "") -> Update:
        return Update(
            id=id_,
            url=f"https://x.com/source/status/{id_}",
            author="source",
            author_name="Source",
            text=text,
            created_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
            conversation_id=conversation,
        )

    def test_equal_timestamp_numeric_and_synthetic_ids_never_type_error(self):
        updates = [
            self.make("abc", "JEONGHAN live update"),
            self.make("10", "JEONGHAN live update"),
        ]
        groups = organize_updates(updates)
        self.assertEqual(len(groups), 1)
        self.assertEqual([item.id for item in groups[0].updates], ["10", "abc"])

    def test_live_part_number_remains_primary_over_equal_timestamp_id(self):
        updates = [
            self.make("abc", "JEONGHAN live part 2", conversation="thread"),
            self.make("10", "JEONGHAN live part 1", conversation="thread"),
        ]
        groups = organize_updates(updates)
        self.assertEqual(len(groups), 1)
        self.assertEqual([item.id for item in groups[0].updates], ["10", "abc"])


if __name__ == "__main__":
    unittest.main()
