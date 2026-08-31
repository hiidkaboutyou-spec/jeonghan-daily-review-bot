from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.archive_store import ArchiveStore
from app.message_delivery import MessageDeliveryStore
from app.models import EventGroup, MediaItem, Update
from app.phase3_recovery import _checkpoint_id
from app.realtime_ingest import realtime_shadow_enabled
from app.state import StateCorruptionError, StateStore
from app.zero_silent_miss import media_asset_id, translation_job_id


class DurableStateArchitectureTests(unittest.TestCase):
    @staticmethod
    def update(identifier: str, *, media_url: str = "", event_key: str = "") -> Update:
        media = [MediaItem(kind="video", url=media_url)] if media_url else []
        return Update(
            id=identifier,
            url=f"https://x.com/source/status/{identifier}",
            author="source",
            author_name="Source",
            text=f"update {identifier}",
            created_at=datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc),
            media=media,
            event_key=event_key,
            event_title="shared event" if event_key else "",
        )

    def test_true_first_run_can_start_with_fresh_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            state = StateStore(path)
            self.assertFalse(path.exists())
            self.assertEqual(state.data["pending_delivery"], [])
            self.assertEqual(state.data["seen"], {})
            self.assertIn("update_lifecycle", state.data)
            self.assertIn("x_retrieval_checkpoints", state.data)

    def test_malformed_json_fails_closed_and_preserves_forensic_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('{"seen": {"1": "secret-marker"}', encoding="utf-8")

            with self.assertRaises(StateCorruptionError) as raised:
                StateStore(path)

            self.assertFalse(path.exists())
            broken = path.with_suffix(".broken.json")
            self.assertTrue(broken.exists())
            self.assertIn("malformed JSON", str(raised.exception))
            self.assertNotIn("secret-marker", str(raised.exception))

    def test_non_object_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text('["not", "canonical", "state"]', encoding="utf-8")

            with self.assertRaises(StateCorruptionError):
                StateStore(path)

            self.assertFalse(path.exists())
            self.assertTrue(path.with_suffix(".broken.json").exists())

    def test_partial_nested_legacy_state_is_normalized_not_treated_as_top_level_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "seen": "invalid-nested-type",
                        "archive": {},
                        "pending_delivery": "invalid-nested-type",
                    }
                ),
                encoding="utf-8",
            )

            state = StateStore(path)

            self.assertEqual(state.data["seen"], {})
            self.assertEqual(state.data["pending_delivery"], [])
            self.assertIsInstance(state.data["update_lifecycle"], dict)
            self.assertIsInstance(state.data["x_retrieval_checkpoints"], dict)

    def test_pending_lifecycle_and_retry_state_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            update = self.update("restart")
            state = StateStore(path)
            state.queue_updates([update])
            state.record_update_state(
                update,
                status="retry_pending",
                stage="telegram_delivery",
                error_class="TelegramError",
                reason="transport_failure",
            )
            state.data["translation_retry_after"] = "2026-08-15T02:00:00+00:00"
            state.save()

            restored = StateStore(path)
            lifecycle = restored.get_update_state(update.id)

            self.assertEqual(len(restored.data["pending_delivery"]), 1)
            self.assertEqual(lifecycle["status"], "retry_pending")
            self.assertEqual(lifecycle["stage"], "telegram_delivery")
            self.assertEqual(lifecycle["retry_count"], 1)
            self.assertEqual(
                restored.data["translation_retry_after"],
                "2026-08-15T02:00:00+00:00",
            )

    def test_update_id_dedupe_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            update = self.update("dedupe")
            state = StateStore(path)
            state.queue_updates([update, update])
            state.save()

            restored = StateStore(path)
            restored.queue_updates([update])
            restored.save()

            again = StateStore(path)
            ids = [str(item["id"]) for item in again.data["pending_delivery"]]
            self.assertEqual(ids, [update.id])

    def test_seen_state_prevents_pending_redelivery_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            update = self.update("seen")
            state = StateStore(path)
            state.queue_updates([update])
            state.mark_seen(update)
            state.save()

            restored = StateStore(path)
            self.assertTrue(restored.is_seen(update.id))
            self.assertEqual(restored.pop_pending(10), [])
            self.assertEqual(restored.data["pending_delivery"], [])

    def test_delivery_plan_and_receipt_survive_sqlite_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "private-review.sqlite3"
            first = MessageDeliveryStore(db)
            first.save_plan("draft:durable", "hello", ["hello"])
            first.confirm("draft:durable", 0, "hello", 919)
            first.close()

            second = MessageDeliveryStore(db)
            self.assertEqual(second.get_plan("draft:durable"), ("hello", ["hello"]))
            self.assertEqual(
                second.confirmed_message_id("draft:durable", 0, "hello"),
                919,
            )
            second.close()

    def test_delivery_plan_is_immutable_for_same_delivery_key(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "private-review.sqlite3"
            store = MessageDeliveryStore(db)
            store.save_plan("draft:stable", "original", ["original"])
            store.save_plan("draft:stable", "different", ["different"])
            self.assertEqual(
                store.get_plan("draft:stable"),
                ("original", ["original"]),
            )
            store.close()

    def test_phase3_checkpoint_coexists_with_pending_lifecycle_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            update = self.update("checkpoint")
            now = datetime.now(timezone.utc)
            start = now - timedelta(hours=2)
            checkpoint_id = _checkpoint_id("source", start, True)
            checkpoint = {
                "version": 1,
                "checkpoint_id": checkpoint_id,
                "source": "source",
                "include_replies": True,
                "window_start": start.isoformat(),
                "segment_start": start.isoformat(),
                "segment_end": now.isoformat(),
                "next_cursor": "opaque-cursor",
                "pages_completed": 1,
                "raw_seen": 5,
                "retry_count": 0,
                "updates": [],
                "updated_at": now.isoformat(),
            }

            state = StateStore(path)
            state.queue_updates([update])
            state.save_x_retrieval_checkpoint(checkpoint)

            restored = StateStore(path)
            recovered = restored.get_x_retrieval_checkpoint(
                source="source",
                start=start,
                end=now,
                include_replies=True,
                allow_older=False,
            )

            self.assertIsNotNone(recovered)
            self.assertEqual(recovered["checkpoint_id"], checkpoint_id)
            self.assertEqual(recovered["next_cursor"], "opaque-cursor")
            self.assertEqual(
                [str(item["id"]) for item in restored.data["pending_delivery"]],
                [update.id],
            )
            self.assertEqual(
                restored.get_update_state(update.id)["status"],
                "pending_delivery",
            )

    def test_translation_job_identity_is_stable_per_logical_update(self):
        first = translation_job_id("processing-group", "100")
        second = translation_job_id("processing-group", "100")
        other = translation_job_id("processing-group", "101")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_distinct_concert_media_remain_distinct_assets(self):
        fancam_a = media_asset_id("video", "https://media.example/fancam-a.mp4")
        fancam_b = media_asset_id("video", "https://media.example/fancam-b.mp4")
        self.assertNotEqual(fancam_a, fancam_b)
        self.assertEqual(
            fancam_a,
            media_asset_id("video", "https://media.example/fancam-a.mp4"),
        )

    def test_processing_group_keeps_original_update_identities(self):
        first = self.update(
            "concert-1",
            media_url="https://media.example/a.mp4",
            event_key="concert:future",
        )
        second = self.update(
            "concert-2",
            media_url="https://media.example/b.mp4",
            event_key="concert:future",
        )
        group = EventGroup("concert:future", "concert", "Concert", [first, second])

        self.assertEqual([item.id for item in group.updates], ["concert-1", "concert-2"])
        self.assertNotEqual(group.updates[0].media[0].url, group.updates[1].media[0].url)

    def test_archive_event_key_does_not_collapse_source_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "private-review.sqlite3"
            store = ArchiveStore(db)
            first = self.update("event-a", event_key="future:event")
            second = self.update("event-b", event_key="future:event")
            store.index_update(first)
            store.index_update(second)

            rows = store.conn.execute(
                "SELECT update_id, event_key FROM archive_records ORDER BY update_id"
            ).fetchall()
            self.assertEqual(
                [(str(row[0]), str(row[1])) for row in rows],
                [("event-a", "future:event"), ("event-b", "future:event")],
            )
            store.close()

    def test_realtime_shadow_is_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(realtime_shadow_enabled())

    def test_configured_source_set_remains_24_unique_sources(self):
        raw = json.loads(Path("config/sources.json").read_text(encoding="utf-8"))
        sources = raw["sources"]
        handles = [str(item["handle"]).lower() for item in sources]
        self.assertEqual(len(handles), 24)
        self.assertEqual(len(set(handles)), 24)
        self.assertEqual(
            [str(item["handle"]).lower() for item in sources if not item.get("enabled", True)],
            ["flamehanie"],
        )

    def test_private_review_boundary_remains_enabled(self):
        settings = json.loads(Path("config/settings.json").read_text(encoding="utf-8"))
        self.assertTrue(settings["runtime"]["review_only"])


if __name__ == "__main__":
    unittest.main()
