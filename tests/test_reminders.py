from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.reminder_queue import ReminderStore
from app.private_inbox_ui import inbox_draft_keyboard


class ReminderTests(unittest.TestCase):
    def test_due_reminder_persists_and_is_sent_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.sqlite3"
            first = ReminderStore(path)
            job = first.add("draft1", datetime.now(timezone.utc) - timedelta(minutes=1), label="test")
            self.assertEqual([item.id for item in first.due(datetime.now(timezone.utc))], [job.id])
            first.close()
            second = ReminderStore(path)
            second.mark_sent(job.id, datetime.now(timezone.utc))
            self.assertEqual(second.due(datetime.now(timezone.utc)), [])
            self.assertEqual(second.get(job.id).status, "sent")
            second.close()

    def test_pinned_job_is_never_due_and_can_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            store = ReminderStore(Path(temp) / "private.sqlite3")
            job = store.add("draft1", None, label="pin")
            self.assertEqual(job.status, "pinned")
            self.assertEqual(store.due(datetime.now(timezone.utc)), [])
            self.assertTrue(store.cancel(job.id))
            self.assertEqual(store.get(job.id).status, "cancelled")
            store.close()

    def test_inbox_reminder_callbacks_fit_and_have_no_channel_publish(self):
        markup = inbox_draft_keyboard("a1b2c3d4e5f6", "pending", 0)
        values = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in values))
        joined = " ".join(values).lower()
        self.assertNotIn("channel", joined)
        self.assertNotIn("publish", joined)


if __name__ == "__main__":
    unittest.main()
