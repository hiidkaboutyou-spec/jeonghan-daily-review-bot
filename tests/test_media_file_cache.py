from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.media_file_cache import MediaFileCache
from app.models import MediaItem
from app.private_telegram import PrivateReviewTelegramBot, telegram_file_identity
from app.telegram import TelegramError


class MediaFileCacheTests(unittest.TestCase):
    def item(self, kind="photo", suffix="1"):
        return MediaItem(kind=kind, url=f"https://media.example/{suffix}")

    def test_first_upload_cache_and_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "private.sqlite3"
            item = self.item()
            first = MediaFileCache(path)
            self.assertIsNone(first.get(item))
            first.put(item, "file-1", "unique-1")
            first.close()
            second = MediaFileCache(path)
            cached = second.get(item)
            self.assertEqual(cached.file_id, "file-1")
            self.assertEqual(cached.file_unique_id, "unique-1")
            second.close()

    def test_refresh_replaces_invalid_cache_value(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MediaFileCache(Path(temp) / "private.sqlite3")
            item = self.item()
            store.put(item, "old")
            store.delete(item)
            self.assertIsNone(store.get(item))
            store.put(item, "new")
            self.assertEqual(store.get(item).file_id, "new")
            store.close()

    def test_media_group_requires_all_items_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MediaFileCache(Path(temp) / "private.sqlite3")
            one, two = self.item("photo", "1"), self.item("video", "2")
            store.put(one, "photo-file")
            self.assertIsNone(store.get_all([one, two]))
            store.put(two, "video-file")
            cached = store.get_all([one, two])
            self.assertEqual([item.file_id for item in cached], ["photo-file", "video-file"])
            store.close()

    def test_old_unversioned_video_file_id_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            store = MediaFileCache(Path(temp) / "private.sqlite3")
            item = self.item("video", "old.mp4")
            old_key = hashlib.sha256(f"{item.kind}\n{item.url}".encode("utf-8")).hexdigest()
            with store.conn:
                store.conn.execute(
                    "INSERT INTO telegram_media_cache(media_key,kind,original_url,file_id) VALUES(?,?,?,?)",
                    (old_key, item.kind, item.url, "broken-file-id"),
                )
            self.assertIsNone(store.get(item))
            store.close()

    def test_cached_single_and_group_use_private_review_chat(self):
        bot = PrivateReviewTelegramBot("token", 1, 99)
        bot.api = MagicMock(side_effect=[{"photo": [{"file_id": "p"}]}, [{"photo": []}, {"video": {}}]])
        from app.media_file_cache import CachedTelegramMedia
        photo = CachedTelegramMedia("a", "photo", "cached-photo")
        video = CachedTelegramMedia("b", "video", "cached-video")
        bot.send_cached_media([photo])
        self.assertEqual(bot.api.call_args_list[0].kwargs["data"]["chat_id"], 99)
        bot.send_cached_media([photo, video])
        group_data = bot.api.call_args_list[1].kwargs["data"]
        self.assertEqual(group_data["chat_id"], 99)
        self.assertIn("cached-photo", group_data["media"])
        self.assertIn("cached-video", group_data["media"])

    def test_file_identity_from_telegram_response(self):
        photo = {"photo": [{"file_id": "small"}, {"file_id": "best", "file_unique_id": "u"}]}
        self.assertEqual(telegram_file_identity(photo, "photo"), ("best", "u"))
        video = {"video": {"file_id": "vid", "file_unique_id": "vu"}}
        self.assertEqual(telegram_file_identity(video, "video"), ("vid", "vu"))


if __name__ == "__main__":
    unittest.main()
