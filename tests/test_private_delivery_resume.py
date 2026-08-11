from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.ai import GroupCopy
from app.main import short_id
from app.models import EventGroup, Update
from app.private_runtime import PrivateReviewApplication
from app.state import StateStore
from app.telegram import TelegramError


class PrivateDeliveryResumeTests(unittest.TestCase):
    @staticmethod
    def update(identifier: str, minute: int) -> Update:
        return Update(
            id=identifier,
            url=f"https://x.com/source/status/{identifier}",
            author="source",
            author_name="Source",
            text=f"source {identifier}",
            created_at=datetime(2026, 8, 9, 20, minute, tzinfo=timezone.utc),
            conversation_id="thread-1",
        )

    def test_failed_group_retry_reuses_all_persisted_captions_without_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            updates = [self.update("1", 1), self.update("2", 2)]
            group = EventGroup(key="thread:thread-1", category="general", title="old", updates=updates)
            app = PrivateReviewApplication.__new__(PrivateReviewApplication)
            app.state = StateStore(Path(temp) / "state.json")
            app.settings = SimpleNamespace(themes={"themes": {"general": {}}})
            app.archive_db = Mock()
            app.inbox = Mock()
            app._deliver_private_media = AsyncMock()
            app.writer = Mock()
            app.writer.write_group.return_value = GroupCopy(
                title="عنوان",
                category="general",
                bodies={"1": "ترجمهٔ اول", "2": "ترجمهٔ دوم"},
            )
            app.themes = Mock()
            app.themes.caption.side_effect = ["کپشن کامل اول", "کپشن کامل دوم"]
            app.telegram = Mock()
            app.telegram.send_message.side_effect = [
                {"message_id": 100},
                TelegramError("simulated first caption failure"),
            ]

            with patch("app.private_runtime.organize_updates", return_value=[group]):
                with self.assertRaises(TelegramError):
                    asyncio.run(app.deliver_updates(updates, force=False))

                first_id = short_id("scheduled:thread:thread-1:1")
                second_id = short_id("scheduled:thread:thread-1:2")
                restarted_state = StateStore(Path(temp) / "state.json")
                self.assertEqual(restarted_state.get_draft(first_id).caption, "کپشن کامل اول")
                self.assertEqual(restarted_state.get_draft(second_id).caption, "کپشن کامل دوم")
                app.state = restarted_state
                app.writer.write_group.assert_called_once_with(group)

                app.telegram.send_message.reset_mock()
                app.telegram.send_message.side_effect = [
                    {"message_id": 101},
                    {"message_id": 102},
                    {"message_id": 103},
                ]
                asyncio.run(app.deliver_updates(updates, force=False))

            # Retry sends the already persisted captions and never regenerates the
            # complete group through Gemini/the writer.
            app.writer.write_group.assert_called_once_with(group)
            delivered = [call.args[0] for call in app.telegram.send_message.call_args_list[1:]]
            self.assertEqual(delivered, ["کپشن کامل اول", "کپشن کامل دوم"])
            self.assertEqual(
                [call.kwargs["delivery_key"] for call in app.telegram.send_message.call_args_list[1:]],
                [f"draft:{first_id}", f"draft:{second_id}"],
            )

    def test_translation_outage_is_not_sent_or_marked_seen(self):
        with tempfile.TemporaryDirectory() as temp:
            update = self.update("outage", 1)
            group = EventGroup(key="single:outage", category="general", title="old", updates=[update])
            app = PrivateReviewApplication.__new__(PrivateReviewApplication)
            app.state = StateStore(Path(temp) / "state.json")
            app.settings = SimpleNamespace(themes={"themes": {"general": {}}})
            app.archive_db = Mock()
            app.inbox = Mock()
            app._deliver_private_media = AsyncMock()
            app.writer = Mock()
            app.writer.write_group.return_value = GroupCopy(
                title="عنوان",
                category="general",
                bodies={"outage": "⚠️ ترجمهٔ خودکار در دسترس نبود؛ متن اصلی برای بررسی:\n\nsource"},
            )
            app.themes = Mock()
            app.telegram = Mock()
            app._notify_translation_outage_if_due = Mock()

            with patch("app.private_runtime.organize_updates", return_value=[group]):
                asyncio.run(app.deliver_updates([update], force=False))

            app.telegram.send_message.assert_called_once()  # batch-status message only
            app._notify_translation_outage_if_due.assert_called_once_with(1)
            self.assertFalse(app.state.is_seen(update.id))
            self.assertEqual(app.state.data["drafts"], {})

    def test_recent_translation_outage_pauses_pending_retry_loop(self):
        app = PrivateReviewApplication.__new__(PrivateReviewApplication)
        app.state = Mock()
        app.state.data = {"translation_outage_notice": datetime.now(timezone.utc).isoformat()}
        app.settings = SimpleNamespace(runtime={"max_auto_items_per_run": 1000})
        app.deliver_updates = AsyncMock()

        asyncio.run(app.deliver_pending())

        app.state.pop_pending.assert_not_called()
        app.deliver_updates.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
