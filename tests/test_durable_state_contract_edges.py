from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.fic_state import FicObservation, FicStateStore
from app.message_delivery import MessageDeliveryStore
from app.models import MediaItem, Update
from app.state import StateStore
from app.zero_silent_miss import media_asset_id, translation_job_id


class DurableStateContractEdgeTests(unittest.TestCase):
    @staticmethod
    def update(identifier: str, *, event_key: str = "") -> Update:
        return Update(
            id=identifier,
            url=f"https://x.com/source/status/{identifier}",
            author="source",
            author_name="Source",
            text=f"update {identifier}",
            created_at=datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc),
            event_key=event_key,
            event_title="future event" if event_key else "",
            media=[MediaItem(kind="video", url=f"https://media.example/{identifier}.mp4")],
        )

    def test_translation_and_media_lifecycle_restore_together(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            update = self.update("media-restart")
            asset_id = media_asset_id(update.media[0].kind, update.media[0].url)
            job_id = translation_job_id("processing:media-restart", update.id)

            state = StateStore(path)
            state.queue_updates([update])
            state.record_update_state(
                update,
                status="pending_media",
                stage="media",
                translation_status="success",
                translation_job_id=job_id,
                media_status="pending",
                media_asset_ids=asset_id,
            )
            state.save()

            restored = StateStore(path)
            lifecycle = restored.get_update_state(update.id)
            self.assertEqual(lifecycle["status"], "pending_media")
            self.assertEqual(lifecycle["stage"], "media")
            self.assertEqual(lifecycle["translation_status"], "success")
            self.assertEqual(lifecycle["translation_job_id"], job_id)
            self.assertEqual(lifecycle["media_status"], "pending")
            self.assertEqual(lifecycle["media_asset_ids"], asset_id)
            self.assertEqual(
                [str(row["id"]) for row in restored.data["pending_delivery"]],
                [update.id],
            )

    def test_fanfic_state_remains_independent_from_daily_update_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            daily_path = root / "state.json"
            fic_path = root / "fic-state.sqlite3"
            update = self.update("daily-only")

            daily = StateStore(daily_path)
            daily.queue_updates([update])
            daily.save()

            fic = FicStateStore(fic_path)
            status = fic.classify(
                FicObservation(
                    work_id="ao3-123",
                    chapters="2/5",
                    updated="2026-08-15",
                )
            )
            fic.close()
            self.assertEqual(status, "new")

            restored_daily = StateStore(daily_path)
            self.assertEqual(
                [str(row["id"]) for row in restored_daily.data["pending_delivery"]],
                [update.id],
            )
            self.assertNotIn("fic_work_state", restored_daily.data)

            restored_fic = FicStateStore(fic_path)
            self.assertEqual(
                restored_fic.classify(
                    FicObservation(
                        work_id="ao3-123",
                        chapters="2/5",
                        updated="2026-08-15",
                    )
                ),
                "unchanged",
            )
            restored_fic.close()

    def test_delivery_plan_without_receipt_never_claims_confirmed_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private-review.sqlite3"
            first = MessageDeliveryStore(path)
            first.save_plan("draft:crash-window", "body", ["body"])
            first.close()

            restarted = MessageDeliveryStore(path)
            self.assertEqual(
                restarted.get_plan("draft:crash-window"),
                ("body", ["body"]),
            )
            self.assertIsNone(
                restarted.confirmed_message_id("draft:crash-window", 0, "body")
            )
            restarted.close()

    def test_future_event_identity_does_not_participate_in_media_asset_identity(self):
        shared_url = "https://media.example/shared-fancam.mp4"
        first = self.update("event-a", event_key="future:event:a")
        second = self.update("event-b", event_key="future:event:b")
        first.media = [MediaItem(kind="video", url=shared_url)]
        second.media = [MediaItem(kind="video", url=shared_url)]

        self.assertNotEqual(first.event_key, second.event_key)
        self.assertEqual(
            media_asset_id(first.media[0].kind, first.media[0].url),
            media_asset_id(second.media[0].kind, second.media[0].url),
        )


if __name__ == "__main__":
    unittest.main()
