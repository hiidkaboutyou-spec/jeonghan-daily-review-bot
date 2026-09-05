from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import Draft, Update
from app.review_inbox import ReviewInboxStore
from app.source_first_inbox_ui import source_first_draft_keyboard, source_first_keyboard
from app.source_first_queue import SourceFirstQueueStore


class SourceFirstQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private-review.sqlite3"
        self.inbox = ReviewInboxStore(self.path)
        self.queue = SourceFirstQueueStore(self.path)
        self.sources = [
            {"handle": "alpha", "enabled": True, "priority": 99},
            {"handle": "disabled", "enabled": False, "priority": 1},
            {"handle": "beta", "enabled": True, "priority": 1},
        ]

    def tearDown(self):
        self.queue.close()
        self.inbox.close()
        self.temp.cleanup()

    def add(self, draft_id: str, update_id: str, source: str, created_at: str):
        update = Update(
            id=update_id,
            url=f"https://x.com/{source}/status/{update_id}",
            author=source,
            author_name=source,
            text="JEONGHAN",
            created_at=datetime.fromisoformat(created_at).astimezone(timezone.utc),
            category="update",
        )
        draft = Draft(
            id=draft_id,
            update_id=update_id,
            event_key="event",
            caption=draft_id,
            created_at=created_at,
        )
        self.inbox.upsert(draft, update)

    def test_config_order_wins_over_time_and_priority(self):
        self.add("a-new", "1", "alpha", "2026-09-05T12:00:00+00:00")
        self.add("a-old", "2", "alpha", "2026-09-05T10:00:00+00:00")
        self.add("b-oldest", "3", "beta", "2026-09-05T09:00:00+00:00")
        session = self.queue.sync(self.sources)
        snap = self.queue.snapshot(session)
        self.assertEqual(snap.active_source, "alpha")
        self.assertEqual(snap.current_draft_id, "a-old")
        self.assertEqual((snap.active_position, snap.total_sources), (1, 2))
        self.assertEqual((snap.current_item_number, snap.current_item_total), (1, 2))

    def test_source_must_finish_before_next_source(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        self.add("a2", "2", "alpha", "2026-09-05T11:00:00+00:00")
        self.add("b1", "3", "beta", "2026-09-05T09:00:00+00:00")
        session = self.queue.sync(self.sources)
        self.inbox.set_status("a1", "ready")
        self.queue.sync(self.sources)
        snap = self.queue.snapshot(session)
        self.assertEqual(snap.active_source, "alpha")
        self.assertEqual(snap.current_draft_id, "a2")
        self.assertEqual(snap.current_item_number, 2)

        self.inbox.set_status("a2", "rejected")
        self.queue.sync(self.sources)
        snap = self.queue.snapshot(session)
        self.assertEqual(snap.active_source, "beta")
        self.assertEqual(snap.current_draft_id, "b1")

    def test_defer_is_explicit_and_resume_does_not_interrupt_active_source(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        self.add("b1", "2", "beta", "2026-09-05T11:00:00+00:00")
        session = self.queue.sync(self.sources)
        self.assertTrue(self.queue.defer(session, "alpha"))
        self.assertEqual(self.queue.snapshot(session).active_source, "beta")
        self.assertTrue(self.queue.resume(session, "alpha"))
        self.assertEqual(self.queue.snapshot(session).active_source, "beta")
        self.inbox.set_status("b1", "ready")
        self.queue.sync(self.sources)
        self.assertEqual(self.queue.snapshot(session).active_source, "alpha")

    def test_restart_preserves_active_and_deferred_state(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        self.add("b1", "2", "beta", "2026-09-05T11:00:00+00:00")
        session = self.queue.sync(self.sources)
        self.queue.defer(session, "alpha")
        self.queue.close()
        self.queue = SourceFirstQueueStore(self.path)
        self.queue.sync(self.sources)
        snap = self.queue.snapshot(session)
        self.assertEqual(snap.active_source, "beta")
        self.assertIn("alpha", snap.deferred_sources)

    def test_new_pending_item_reopens_source_without_stealing_current_focus(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        self.add("b1", "2", "beta", "2026-09-05T11:00:00+00:00")
        session = self.queue.sync(self.sources)
        self.inbox.set_status("a1", "ready")
        self.queue.sync(self.sources)
        self.assertEqual(self.queue.snapshot(session).active_source, "beta")
        self.add("a2", "3", "alpha", "2026-09-05T12:00:00+00:00")
        self.queue.sync(self.sources)
        self.assertEqual(self.queue.snapshot(session).active_source, "beta")
        self.inbox.set_status("b1", "ready")
        self.queue.sync(self.sources)
        self.assertEqual(self.queue.snapshot(session).active_source, "alpha")

    def test_completeness_is_visible_but_not_queue_authority(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        self.queue.conn.execute(
            """
            CREATE TABLE completeness_attempts(
                sequence INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                error_class TEXT NOT NULL,
                finalized INTEGER NOT NULL
            )
            """
        )
        self.queue.conn.execute(
            "INSERT INTO completeness_attempts VALUES(1,'alpha','unproven','ProviderGap',1)"
        )
        self.queue.conn.commit()
        session = self.queue.sync(self.sources)
        snap = self.queue.snapshot(session)
        alpha = next(item for item in snap.sources if item.source == "alpha")
        self.assertEqual(alpha.completeness_status, "unproven")
        self.assertEqual(alpha.completeness_error, "ProviderGap")
        self.assertEqual(snap.active_source, "alpha")

    def test_retry_metadata_persists(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        session = self.queue.sync(self.sources)
        self.assertTrue(self.queue.begin_retry(session, "alpha"))
        self.assertTrue(self.queue.finish_retry(session, "alpha", success=False, error="XCollectionError"))
        snap = self.queue.snapshot(session)
        alpha = next(item for item in snap.sources if item.source == "alpha")
        self.assertEqual(alpha.retry_count, 1)
        self.assertEqual(alpha.last_retry_status, "failed")

    def test_ui_callbacks_are_safe_and_keep_legacy_view(self):
        self.add("a1", "1", "alpha", "2026-09-05T10:00:00+00:00")
        session = self.queue.sync(self.sources)
        snap = self.queue.snapshot(session)
        markups = [
            source_first_keyboard(snap),
            source_first_draft_keyboard("a1", "alpha"),
        ]
        callbacks = [
            button["callback_data"]
            for markup in markups
            for row in markup["inline_keyboard"]
            for button in row
        ]
        self.assertIn("sq:legacy:pending:0", callbacks)
        self.assertTrue(any(value.startswith("sq:defer:") for value in callbacks))
        self.assertTrue(any(value.startswith("sq:retry:") for value in callbacks))
        self.assertNotIn("publish", " ".join(callbacks).lower())
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callbacks))


if __name__ == "__main__":
    unittest.main()
