from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import Draft, Update
from app.private_inbox_ui import inbox_draft_keyboard, inbox_list_keyboard
from app.review_inbox import ReviewInboxStore


class ReviewInboxTests(unittest.TestCase):
    def make_update(self, id_: str, source: str = "source", category: str = "live") -> Update:
        return Update(id=id_, url=f"https://x.com/{source}/status/{id_}", author=source, author_name=source, text="JEONGHAN update", created_at=datetime.now(timezone.utc), category=category)

    def make_draft(self, id_: str, update_id: str) -> Draft:
        return Draft(id=id_, update_id=update_id, event_key="event", caption=f"caption {id_}", created_at=datetime.now(timezone.utc).isoformat())

    def test_persistence_status_and_filters(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.sqlite3"
            first = ReviewInboxStore(path)
            first.upsert(self.make_draft("d1", "u1"), self.make_update("u1", "alpha", "live"))
            first.upsert(self.make_draft("d2", "u2"), self.make_update("u2", "beta", "airport"))
            self.assertTrue(first.set_status("d2", "rejected"))
            first.close()
            second = ReviewInboxStore(path)
            self.assertEqual(second.count("pending"), 1)
            self.assertEqual(second.count("rejected"), 1)
            items, _, _ = second.list_items(status="pending", source="alpha")
            self.assertEqual([item.draft_id for item in items], ["d1"])
            second.close()

    def test_pagination(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReviewInboxStore(Path(temp) / "private.sqlite3")
            for i in range(12):
                store.upsert(self.make_draft(f"d{i}", f"u{i}"), self.make_update(f"u{i}"))
            items, page, pages = store.list_items(status="pending", page=1, page_size=5)
            self.assertEqual((page, pages, len(items)), (1, 3, 5))
            store.close()

    def test_sync_existing_state_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReviewInboxStore(Path(temp) / "private.sqlite3")
            update = self.make_update("u1")
            draft = self.make_draft("d1", "u1")
            self.assertEqual(store.sync_from_state({"d1": draft.to_dict()}, {"u1": update.to_dict()}), 1)
            store.set_status("d1", "ready")
            self.assertEqual(store.sync_from_state({"d1": draft.to_dict()}, {"u1": update.to_dict()}), 0)
            self.assertEqual(store.get("d1").status, "ready")
            store.close()

    def test_inbox_ui_has_no_publish_action(self):
        item = type("Item", (), {"draft_id":"d1","status":"pending","source":"s","category":"live"})()
        list_markup = inbox_list_keyboard([item], "pending", 0, 1)
        draft_markup = inbox_draft_keyboard("d1", "pending", 0)
        callbacks = [button["callback_data"] for markup in (list_markup, draft_markup) for row in markup["inline_keyboard"] for button in row]
        joined = " ".join(callbacks).lower()
        self.assertNotIn("publish", joined)
        self.assertNotIn("channel", joined)
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callbacks))


if __name__ == "__main__":
    unittest.main()
