from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.channel_style_application import ChannelStyleReviewApplication
from app.media import PreparedMedia
from app.media_delivery import MediaDeliveryLedger
from app.media_delivery_runtime import MediaDedupReviewApplication
from app.models import MediaItem, Update
from app.telegram import TelegramError


class MediaDeliveryLedgerTests(unittest.TestCase):
    def item(self, url: str = "https://media.example/one.jpg") -> MediaItem:
        return MediaItem(kind="photo", url=url)

    def test_url_receipt_blocks_recent_repeat_but_expires(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = MediaDeliveryLedger(Path(temp) / "private.sqlite3", dedup_hours=72)
            item = self.item()
            identities = ledger.identities_for(item)
            now = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)
            self.assertFalse(ledger.any_recent(identities, now=now))
            ledger.mark_delivered(identities, kind="photo", update_id="1", delivered_at=now)
            self.assertTrue(ledger.any_recent(identities, now=now + timedelta(hours=71)))
            self.assertFalse(ledger.any_recent(identities, now=now + timedelta(hours=73)))
            ledger.close()

    def test_content_hash_matches_same_bytes_from_different_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            one = root / "one.jpg"
            two = root / "two.jpg"
            one.write_bytes(b"same exact image bytes")
            two.write_bytes(b"same exact image bytes")
            ledger = MediaDeliveryLedger(root / "private.sqlite3")
            self.assertEqual(ledger.content_identity(one), ledger.content_identity(two))
            ledger.close()

    def test_file_unique_id_dedups_different_source_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = MediaDeliveryLedger(Path(temp) / "private.sqlite3")
            one = self.item("https://one.example/photo.jpg")
            two = self.item("https://two.example/copy.jpg")
            first = ledger.identities_for(one, file_unique_id="telegram-stable-id")
            second = ledger.identities_for(two, file_unique_id="telegram-stable-id")
            ledger.mark_delivered(first, kind="photo", update_id="1")
            self.assertTrue(ledger.any_recent(second))
            ledger.close()

    def test_production_application_includes_media_dedup_runtime(self):
        self.assertTrue(issubclass(ChannelStyleReviewApplication, MediaDedupReviewApplication))

    def test_runtime_prefers_canonical_state_store_path(self):
        with tempfile.TemporaryDirectory() as temp:
            canonical = Path(temp) / "state" / "state.json"
            wrong = Path(temp) / "wrong" / "settings.json"

            def fake_parent(app, settings):
                app.state = SimpleNamespace(path=canonical)

            with patch("app.media_delivery_runtime.ReminderReviewApplication.__init__", fake_parent):
                app = MediaDedupReviewApplication(SimpleNamespace(state_path=wrong))
            self.assertEqual(app.media_delivery.path, canonical.with_name("private-review.sqlite3"))
            app.media_delivery.close()

    def test_intentional_no_state_test_double_constructs_without_fake_persistence(self):
        def fake_parent(app, settings):
            pass

        with patch("app.media_delivery_runtime.ReminderReviewApplication.__init__", fake_parent):
            app = MediaDedupReviewApplication(SimpleNamespace())
        self.assertIsNone(app.media_delivery)


class _NoCache:
    def get_all(self, media):
        return None

    def put(self, item, file_id, unique_id=""):
        return None

    def delete(self, item):
        return None


class _PreparedMediaManager:
    def __init__(self, payload: bytes):
        self.payload = payload

    def prepare(self, update):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "photo.jpg"
        path.write_bytes(self.payload)
        return temp, [PreparedMedia("photo", path, "image/jpeg")]


class _EmptyMediaManager:
    def prepare(self, update):
        return tempfile.TemporaryDirectory(), []


class _TelegramRecorder:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def send_media(self, prepared):
        self.calls += 1
        if self.fail:
            raise TelegramError("simulated send failure")
        return [{"photo": [{"file_id": "file-1", "file_unique_id": "unique-1"}]}]

    def send_cached_media(self, cached):
        self.calls += 1
        if self.fail:
            raise TelegramError("simulated cached send failure")
        return [{"photo": [{"file_id": "file-1", "file_unique_id": "unique-1"}]}]


def _update(update_id: str, media_url: str) -> Update:
    return Update(
        id=update_id,
        url=f"https://x.com/source/status/{update_id}",
        author="source",
        author_name="Source",
        text="photo update",
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
        media=[MediaItem(kind="photo", url=media_url)],
    )


class MediaDeliveryRuntimeTests(unittest.TestCase):
    def bare_app(self, db_path: Path, *, payload: bytes = b"same bytes", fail: bool = False):
        app = object.__new__(MediaDedupReviewApplication)
        app.media_cache = _NoCache()
        app.media_delivery = MediaDeliveryLedger(db_path)
        app.media = _PreparedMediaManager(payload)
        app.telegram = _TelegramRecorder(fail=fail)
        return app

    def test_same_exact_bytes_different_urls_are_not_sent_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.bare_app(Path(temp) / "private.sqlite3")
            self.assertTrue(asyncio.run(app._deliver_private_media(_update("1", "https://media.example/a.jpg"))))
            self.assertTrue(asyncio.run(app._deliver_private_media(_update("2", "https://mirror.example/b.jpg"))))
            self.assertEqual(app.telegram.calls, 1)
            app.media_delivery.close()

    def test_all_failed_media_is_reported_to_the_delivery_caller(self):
        with tempfile.TemporaryDirectory() as temp:
            app = self.bare_app(Path(temp) / "private.sqlite3")
            app.media = _EmptyMediaManager()
            result = asyncio.run(app._deliver_private_media(_update("1", "https://media.example/missing.jpg")))
            self.assertFalse(result)
            self.assertEqual(app.telegram.calls, 0)
            app.media_delivery.close()

    def test_failed_telegram_send_does_not_create_false_delivery_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.sqlite3"
            failing = self.bare_app(path, fail=True)
            with self.assertRaises(TelegramError):
                asyncio.run(failing._deliver_private_media(_update("1", "https://media.example/a.jpg")))
            failing.media_delivery.close()

            retry = self.bare_app(path, fail=False)
            asyncio.run(retry._deliver_private_media(_update("1", "https://media.example/a.jpg")))
            self.assertEqual(retry.telegram.calls, 1)
            retry.media_delivery.close()


if __name__ == "__main__":
    unittest.main()
