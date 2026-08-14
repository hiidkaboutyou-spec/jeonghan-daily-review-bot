from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.models import MediaItem, Update
from app.personal_assistant import PersonalAssistantReviewApplication
from app.webhook_aware_assistant import WebhookAwarePersonalAssistant


class ConfiguredSourcesPhase1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
        self.sources = [
            {
                "handle": "trustedsource",
                "enabled": True,
                "include_replies": True,
            }
        ]

    def _update(self, id_: str, author: str, *, minutes_ago: int = 10, media: str = "") -> Update:
        items = []
        if media:
            items = [
                MediaItem(
                    kind=media,
                    url=f"https://media.example/{id_}.{'mp4' if media == 'video' else 'jpg'}",
                    content_type="video/mp4" if media == "video" else "image/jpeg",
                )
            ]
        return Update(
            id=id_,
            url=f"https://x.com/{author}/status/{id_}",
            author=author,
            author_name=author,
            text=f"post {id_}",
            created_at=self.now - timedelta(minutes=minutes_ago),
            media=items,
        )

    def _app(self) -> WebhookAwarePersonalAssistant:
        app = object.__new__(WebhookAwarePersonalAssistant)
        app.collector = SimpleNamespace(
            sources=self.sources,
            last_errors=[],
            collect_window=AsyncMock(),
            collect_source=AsyncMock(),
            search_archive=AsyncMock(),
        )
        app.telegram = SimpleNamespace(send_message=Mock(), edit_message_text=Mock())
        app.settings = SimpleNamespace(
            admin_user_id=1,
            timezone=timezone.utc,
            keyword_groups=[{"terms": ["JEONGHAN"]}],
            runtime={"max_search_candidates": 8, "scheduled_min_interval_minutes": 12, "scheduled_lookback_hours": 24},
        )
        return app

    def test_recent2h_filters_external_author_before_delivery(self):
        app = self._app()
        configured = self._update("configured", "trustedsource")
        external = self._update("external", "randomfan")
        app.collector.collect_window.return_value = [external, configured]
        app.deliver_updates = AsyncMock()

        with patch("app.webhook_aware_assistant.datetime") as clock:
            clock.now.return_value = self.now
            asyncio.run(app.run_recent2h())

        delivered = app.deliver_updates.await_args.args[0]
        self.assertEqual([item.id for item in delivered], ["configured"])

    def test_configured_source24_filters_defensively_and_preserves_media(self):
        app = self._app()
        configured = self._update("video", "trustedsource", media="video")
        external = self._update("external", "randomfan", media="video")
        app.collector.collect_source.return_value = [external, configured]
        app.deliver_updates = AsyncMock()

        with patch("app.webhook_aware_assistant.datetime") as clock:
            clock.now.return_value = self.now
            asyncio.run(app.run_source24("trustedsource"))

        delivered = app.deliver_updates.await_args.args[0]
        self.assertEqual([item.id for item in delivered], ["video"])
        self.assertEqual(delivered[0].media[0].kind, "video")

    def test_unconfigured_source24_never_calls_collector(self):
        app = self._app()

        asyncio.run(app.run_source24("randomfan"))

        app.collector.collect_source.assert_not_awaited()
        self.assertIn("فهرست منابع", app.telegram.send_message.call_args.args[0])

    def test_custom_source24_escape_hatch_is_blocked(self):
        app = self._app()

        asyncio.run(app.run_source24("custom"))

        app.collector.collect_source.assert_not_awaited()
        self.assertIn("فقط برای منابع تنظیم‌شده", app.telegram.send_message.call_args.args[0])

    def test_manual_search_filters_local_and_remote_external_authors(self):
        app = self._app()
        local_ok = self._update("local", "trustedsource")
        local_bad = self._update("local-bad", "randomfan")
        remote_ok = self._update("remote", "trustedsource", minutes_ago=5)
        remote_bad = self._update("remote-bad", "otherfan", minutes_ago=5)
        app.archive_db = SimpleNamespace(
            search=Mock(return_value=[local_bad, local_ok]),
            index_update=Mock(),
        )
        app.collector.search_archive.return_value = [remote_bad, remote_ok]
        app.writer = SimpleNamespace(
            expand_search=Mock(return_value=["JEONGHAN"]),
            candidate_titles=Mock(return_value={}),
        )
        app.state = SimpleNamespace(create_session=Mock())

        asyncio.run(app.run_search("airport"))

        payload = app.state.create_session.call_args.args[1]
        selected_authors = {
            candidate["selected"]["author"] for candidate in payload["candidates"]
        }
        self.assertEqual(selected_authors, {"trustedsource"})
        indexed_authors = {call.args[0].author for call in app.archive_db.index_update.call_args_list}
        self.assertEqual(indexed_authors, {"trustedsource"})

    def test_final_private_delivery_guard_blocks_external_media_recovery(self):
        app = self._app()
        configured = self._update("photo", "trustedsource", media="photo")
        external = self._update("external", "randomfan", media="photo")

        with patch.object(
            PersonalAssistantReviewApplication,
            "deliver_updates",
            new=AsyncMock(),
        ) as parent:
            asyncio.run(app.deliver_updates([external, configured], force=True))

        delivered = parent.await_args.args[0]
        self.assertEqual([item.id for item in delivered], ["photo"])
        self.assertEqual(delivered[0].media[0].kind, "photo")

    def test_stale_pending_external_update_is_dropped_before_replay(self):
        app = self._app()
        configured = self._update("configured", "trustedsource")
        external = self._update("external", "randomfan")
        configured_raw = configured.to_dict()
        configured_raw["force"] = False
        external_raw = external.to_dict()
        external_raw["force"] = False
        app.state = SimpleNamespace(data={"pending_delivery": [external_raw, configured_raw]})

        with patch.object(
            PersonalAssistantReviewApplication,
            "deliver_pending",
            new=AsyncMock(),
        ) as parent:
            asyncio.run(app.deliver_pending())

        self.assertEqual(
            [item["id"] for item in app.state.data["pending_delivery"]],
            ["configured"],
        )
        parent.assert_awaited_once()

    def test_stale_external_search_session_cannot_reconstruct_event(self):
        app = self._app()
        external = self._update("external", "randomfan")
        app.state = SimpleNamespace(
            get_session=Mock(
                return_value={"candidates": [{"selected": external.to_dict()}]}
            )
        )

        with patch.object(
            PersonalAssistantReviewApplication,
            "run_selected_event",
            new=AsyncMock(),
        ) as parent:
            asyncio.run(app.run_selected_event("session", 0))

        parent.assert_not_awaited()
        self.assertIn("منبع تنظیم‌شده نیست", app.telegram.send_message.call_args.args[0])

    def test_scheduled_scan_queues_only_configured_and_oldest_first(self):
        app = self._app()
        previous = self.now - timedelta(hours=1)
        external = self._update("external", "randomfan", minutes_ago=30)
        newest = self._update("newest", "trustedsource", minutes_ago=5)
        oldest = self._update("oldest", "trustedsource", minutes_ago=20)
        app.collector.collect_window.return_value = [newest, external, oldest]
        app.state = SimpleNamespace(
            data={
                "last_auto_run": previous.isoformat(),
                "last_auto_attempt": (self.now - timedelta(minutes=30)).isoformat(),
            },
            is_seen=Mock(return_value=False),
            queue_updates=Mock(),
        )

        with patch("app.webhook_aware_assistant.datetime") as clock:
            clock.now.return_value = self.now
            asyncio.run(app.run_scheduled_scan())

        queued = app.state.queue_updates.call_args.args[0]
        self.assertEqual([item.id for item in queued], ["oldest", "newest"])
        self.assertEqual(app.state.data["last_auto_run"], self.now.isoformat())


if __name__ == "__main__":
    unittest.main()
