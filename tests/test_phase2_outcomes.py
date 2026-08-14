from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.media import PreparedMedia
from app.media_delivery import MediaDeliveryLedger
from app.media_delivery_runtime import MediaDedupReviewApplication
from app.models import Draft, MediaItem, Update
from app.state import StateStore


class _NoCache:
    def get_all(self, media):
        return None

    def put(self, item, file_id, unique_id=""):
        return None

    def delete(self, item):
        return None


class _PartialMediaManager:
    def prepare(self, update):
        temp = tempfile.TemporaryDirectory()
        if update.media[0].url.endswith("missing.jpg"):
            return temp, []
        path = Path(temp.name) / "ok.jpg"
        path.write_bytes(b"prepared photo bytes")
        return temp, [PreparedMedia("photo", path, "image/jpeg")]


class _Telegram:
    def send_media(self, prepared):
        return [{"photo": [{"file_id": "file-ok", "file_unique_id": "unique-ok"}]}]

    def send_cached_media(self, cached):
        raise AssertionError("cache should not be used")


def _update() -> Update:
    return Update(
        id="album-partial",
        url="https://x.com/source/status/album-partial",
        author="source",
        author_name="Source",
        text="two-photo update",
        created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
        media=[
            MediaItem(kind="photo", url="https://media.example/missing.jpg"),
            MediaItem(kind="photo", url="https://media.example/ok.jpg"),
        ],
    )


class Phase2OutcomeTests(unittest.TestCase):
    def test_partial_multi_asset_media_is_not_labeled_full_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            app = object.__new__(MediaDedupReviewApplication)
            app.state = StateStore(root / "state.json")
            app.media_cache = _NoCache()
            app.media_delivery = MediaDeliveryLedger(root / "private-review.sqlite3")
            app.media = _PartialMediaManager()
            app.telegram = _Telegram()
            update = _update()
            app.state.queue_updates([update])

            result = asyncio.run(app._deliver_private_media(update))

            self.assertTrue(result, "one successfully sent asset preserves existing delivery behavior")
            report = app._last_media_delivery_report
            self.assertEqual(report["requested"], 2)
            self.assertEqual(report["sent"], 1)
            self.assertEqual(report["failed"], 1)
            lifecycle = app.state.get_update_state(update.id)
            self.assertEqual(lifecycle["media_status"], "partial_failed")
            self.assertEqual(lifecycle["reason"], "media_partial_failure")

            app.state.save_draft(
                Draft(
                    id="draft-album-partial",
                    update_id=update.id,
                    event_key="single:album-partial",
                    caption="caption",
                    telegram_message_id=515,
                    created_at="2026-08-14T12:01:00+00:00",
                )
            )
            app.state.mark_seen(update)
            lifecycle = app.state.get_update_state(update.id)
            self.assertEqual(lifecycle["status"], "delivered_text_media_failed")
            self.assertEqual(lifecycle["delivery_receipt_id"], 515)
            app.media_delivery.close()

    def test_manual_review_draft_records_fidelity_rejection_without_content(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            update = _update()
            state.queue_updates([update])
            state.save_draft(
                Draft(
                    id="draft-manual",
                    update_id=update.id,
                    event_key="single:album-partial",
                    caption="private caption that must not enter telemetry",
                    telegram_message_id=0,
                    created_at="2026-08-14T12:01:00+00:00",
                    mode="manual_review",
                )
            )
            lifecycle = state.get_update_state(update.id)
            self.assertEqual(lifecycle["translation_status"], "fidelity_rejected")
            self.assertEqual(lifecycle["reason"], "manual_review_required")
            self.assertNotIn("private caption", repr(lifecycle))


if __name__ == "__main__":
    unittest.main()
