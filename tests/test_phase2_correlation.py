from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.models import Draft, Update
from app.state import StateStore
from app.zero_silent_miss import translation_job_id


class Phase2CorrelationTests(unittest.TestCase):
    def test_translation_job_and_first_event_id_stay_stable_across_stage_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            update = Update(
                id="stable-123",
                url="https://x.com/source/status/stable-123",
                author="source",
                author_name="Source",
                text="configured source update",
                created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            )
            state.queue_updates([update])
            first_event = "conversation:thread-1"
            first_job = translation_job_id(first_event, update.id)
            state.record_update_state(
                update,
                status="pending_translation",
                stage="translation",
                event_id=first_event,
                translation_status="requested",
                translation_job_id=first_job,
            )

            # Draft/group code may normalize the same real group label differently.
            # Neither that alternate label nor a retry may create a new logical
            # translation correlation identifier for the same source update.
            state.save_draft(
                Draft(
                    id="draft-stable",
                    update_id=update.id,
                    event_key="thread:thread-1",
                    caption="translated caption",
                    telegram_message_id=0,
                    created_at="2026-08-14T12:01:00+00:00",
                )
            )
            state.record_update_state(
                update,
                status="retry_pending",
                stage="telegram_delivery",
                event_id="single:stable-123",
                translation_job_id=translation_job_id("retry-group", update.id),
                error_class="TelegramError",
                reason="stage_exception",
            )

            lifecycle = state.get_update_state(update.id)
            self.assertEqual(lifecycle["event_id"], first_event)
            self.assertEqual(lifecycle["translation_job_id"], first_job)
            self.assertEqual(first_job, translation_job_id("totally-different-label", update.id))
            self.assertEqual(lifecycle["status"], "retry_pending")


if __name__ == "__main__":
    unittest.main()
