from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.ai import GroupCopy
from app.models import Draft, EventGroup, MediaItem, Update
from app.observability import safe_metadata, scrub_event
from app.private_runtime import PrivateReviewApplication
from app.state import StateStore
from app.telegram import TelegramError
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant
from app.x_client import XCollectionError, XCollector


class ZeroSilentMissTests(unittest.TestCase):
    @staticmethod
    def update(identifier: str = "101", *, media: bool = False) -> Update:
        item = Update(
            id=identifier,
            url=f"https://x.com/source/status/{identifier}",
            author="source",
            author_name="Source",
            text="configured source update",
            created_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
        )
        if media:
            item.media = [MediaItem(kind="photo", url=f"https://pbs.twimg.com/media/{identifier}.jpg")]
        return item

    def test_partial_retrieval_is_durable_and_not_labeled_complete(self):
        update = self.update("partial")
        collector = XCollector(
            {},
            [{"handle": "source", "enabled": True, "include_replies": True}],
            [],
        )
        collector._collect_source_timeline = AsyncMock(side_effect=XCollectionError("provider down"))
        collector._run_queries = AsyncMock(return_value=[update])
        start = update.created_at - timedelta(hours=1)
        end = update.created_at + timedelta(hours=1)

        result = asyncio.run(collector.collect_window(start, end, max_per_query=10))
        self.assertEqual([item.id for item in result], [update.id])
        self.assertTrue(collector.last_errors)
        self.assertEqual(collector.last_retrieval_status, "partial_source_window")

        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            state.queue_updates(result)
            lifecycle = state.get_update_state(update.id)
            self.assertEqual(lifecycle["retrieval_status"], "partial_source_window")
            self.assertEqual(lifecycle["status"], "pending_delivery")

    def test_partial_scheduled_window_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            app = WebhookAwarePersonalAssistant.__new__(WebhookAwarePersonalAssistant)
            app.state = StateStore(Path(temp) / "state.json")
            old = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
            app.state.data["last_auto_run"] = old.isoformat()
            app.state.data["last_auto_attempt"] = ""
            app.settings = SimpleNamespace(runtime={"scheduled_lookback_hours": 24, "scheduled_min_interval_minutes": 1})
            update = self.update("cursor")
            app.collector = SimpleNamespace(
                collect_window=AsyncMock(return_value=[update]),
                last_errors=["@source: XCollectionError"],
                last_retrieval_attempt_id="attempt-test",
            )
            app._record_x_scan_failure = Mock()

            with patch("app.webhook_aware_assistant.datetime") as clock:
                clock.now.return_value = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
                clock.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                asyncio.run(app.run_scheduled_scan())

            self.assertEqual(app.state.data["last_auto_run"], old.isoformat())
            app._record_x_scan_failure.assert_called_once()
            self.assertEqual(len(app.state.data["pending_delivery"]), 1)

    def test_malformed_pending_row_is_quarantined_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            state.data["pending_delivery"] = [
                {"id": "broken", "author": "source", "created_at": "not-a-date", "force": False}
            ]
            self.assertEqual(state.pop_pending(10), [])
            self.assertEqual(state.data["pending_delivery"], [])
            self.assertEqual(len(state.data["quarantined_delivery"]), 1)
            self.assertEqual(state.data["quarantined_delivery"][0]["update_id"], "broken")
            lifecycle = state.get_update_state("broken")
            self.assertEqual(lifecycle["status"], "quarantined_with_reason")
            self.assertEqual(lifecycle["reason"], "invalid_pending_payload")

    def test_media_failure_is_not_collapsed_into_full_success_and_receipt_is_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            update = self.update("media-fail", media=True)
            state.queue_updates([update])
            draft = Draft(
                id="draft-media",
                update_id=update.id,
                event_key="single:media-fail",
                caption="caption",
                telegram_message_id=777,
                created_at="2026-08-14T12:01:00+00:00",
            )
            state.save_draft(draft)
            state.record_update_state(
                update,
                status="pending_delivery",
                stage="telegram_delivery",
                media_status="terminal_failed",
                reason="media_unavailable",
            )
            state.mark_seen(update)
            lifecycle = state.get_update_state(update.id)
            self.assertEqual(lifecycle["status"], "delivered_text_media_failed")
            self.assertEqual(lifecycle["media_status"], "terminal_failed")
            self.assertEqual(lifecycle["delivery_receipt_id"], 777)

    def test_translation_provider_outage_stays_pending_and_recoverable(self):
        with tempfile.TemporaryDirectory() as temp:
            update = self.update("translation")
            group = EventGroup("single:translation", "general", "title", [update])
            app = PrivateReviewApplication.__new__(PrivateReviewApplication)
            app.state = StateStore(Path(temp) / "state.json")
            app.state.queue_updates([update])
            app.settings = SimpleNamespace(themes={"themes": {"general": {}}}, gemini_model="gemini-test")
            app.archive_db = Mock()
            app.inbox = Mock()
            app.writer = Mock()
            app.writer._gemini_circuit_open = "quota"
            app.writer.write_group.return_value = GroupCopy(
                "title",
                "general",
                {update.id: "⚠️ ترجمهٔ خودکار در دسترس نبود؛ متن اصلی برای بررسی:\n\nsource"},
            )
            app.themes = Mock()
            app.telegram = Mock()
            app._notify_translation_outage_if_due = Mock()

            with patch("app.private_runtime.organize_updates", return_value=[group]), patch(
                "app.zero_silent_miss.organize_updates", return_value=[group]
            ):
                asyncio.run(app.deliver_updates([update], force=False))

            self.assertFalse(app.state.is_seen(update.id))
            self.assertEqual(len(app.state.data["pending_delivery"]), 1)
            lifecycle = app.state.get_update_state(update.id)
            self.assertEqual(lifecycle["status"], "pending_translation")
            self.assertEqual(lifecycle["translation_status"], "quota_429")

    def test_telegram_failure_keeps_pending_item_and_records_retry_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            update = self.update("telegram")
            group = EventGroup("single:telegram", "general", "title", [update])
            app = PrivateReviewApplication.__new__(PrivateReviewApplication)
            app.state = StateStore(Path(temp) / "state.json")
            app.state.queue_updates([update])
            app.settings = SimpleNamespace(themes={"themes": {"general": {}}}, gemini_model="gemini-test")
            app.archive_db = Mock()
            app.inbox = Mock()
            app.writer = Mock()
            app.writer.write_group.return_value = GroupCopy("title", "general", {update.id: "translated"})
            app.themes = Mock()
            app.themes.caption.return_value = "caption"
            app.telegram = Mock()
            app.telegram.send_message.side_effect = [
                {"message_id": 1},
                TelegramError("simulated send failure"),
            ]

            with patch("app.private_runtime.organize_updates", return_value=[group]), patch(
                "app.zero_silent_miss.organize_updates", return_value=[group]
            ):
                with self.assertRaises(TelegramError):
                    asyncio.run(app.deliver_updates([update], force=False))

            self.assertFalse(app.state.is_seen(update.id))
            self.assertEqual(len(app.state.data["pending_delivery"]), 1)
            lifecycle = app.state.get_update_state(update.id)
            self.assertEqual(lifecycle["status"], "retry_pending")
            self.assertEqual(lifecycle["stage"], "telegram_delivery")
            self.assertEqual(lifecycle["error_class"], "TelegramError")

    def test_successful_seen_transition_keeps_telegram_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            state = StateStore(Path(temp) / "state.json")
            update = self.update("success")
            state.queue_updates([update])
            state.save_draft(
                Draft(
                    id="draft-success",
                    update_id=update.id,
                    event_key="single:success",
                    caption="caption",
                    telegram_message_id=909,
                    created_at="2026-08-14T12:01:00+00:00",
                )
            )
            state.mark_seen(update)
            lifecycle = state.get_update_state(update.id)
            self.assertEqual(lifecycle["status"], "delivered")
            self.assertEqual(lifecycle["delivery_receipt_id"], 909)
            self.assertEqual(lifecycle["delivery_key"], "draft:draft-success")

    def test_observability_scrubs_private_content_but_preserves_safe_correlation(self):
        event = {
            "message": "private review caption DO_NOT_LEAK",
            "request": {"headers": {"Authorization": "Bearer token"}, "data": "private body"},
            "extra": {"TELEGRAM_BOT_TOKEN": "abc", "X_COOKIE": "def", "GEMINI_API_KEY": "ghi"},
            "tags": {
                "stage": "translation",
                "update_id": "12345",
                "source": "source",
                "delivery_key": "draft:safe",
                "not_allowed_private": "private text",
            },
        }
        safe = scrub_event(event, {})
        rendered = repr(safe)
        self.assertIn("12345", rendered)
        self.assertIn("translation", rendered)
        self.assertIn("draft:safe", rendered)
        self.assertNotIn("DO_NOT_LEAK", rendered)
        self.assertNotIn("Authorization", rendered)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", rendered)
        self.assertNotIn("not_allowed_private", rendered)
        self.assertNotIn("private text", rendered)
        meta = safe_metadata({"event": "x", "source": "source", "unknown": "private"})
        self.assertEqual(meta["source"], "source")
        self.assertNotIn("unknown", meta)


if __name__ == "__main__":
    unittest.main()
