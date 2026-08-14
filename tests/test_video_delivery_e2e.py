from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app.media import MediaManager
from app.media_delivery import MediaDeliveryLedger
from app.media_delivery_runtime import MediaDedupReviewApplication
from app.media_file_cache import MediaFileCache
from app.models import MediaItem, Update
from app.private_telegram import PrivateReviewTelegramBot


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "FFmpeg and ffprobe are required for the video delivery E2E regression",
)
class VideoDeliveryE2ETests(unittest.TestCase):
    def test_v2_cache_miss_redownloads_normalizes_sends_and_caches_v3_video(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db_path = root / "private-review.sqlite3"
            source = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x180:rate=25",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:sample_rate=48000",
                    "-t",
                    "1.2",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    str(source),
                ],
                check=True,
                timeout=30,
            )

            item = MediaItem(
                kind="video",
                url="https://video.twimg.com/ext_tw_video/1/pu/vid/e2e.mp4",
            )
            update = Update(
                id="video-e2e",
                url="https://x.com/source/status/video-e2e",
                author="source",
                author_name="Source",
                text="video update",
                created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
                media=[item],
            )

            app = object.__new__(MediaDedupReviewApplication)
            app.media_cache = MediaFileCache(db_path)
            app.media_delivery = MediaDeliveryLedger(db_path)
            manager = MediaManager({})
            download_calls: list[str] = []

            def fake_download(url, path, max_bytes, attempts=2):
                download_calls.append(url)
                shutil.copyfile(source, path)

            manager._stream_download = fake_download
            manager._download_with_ytdlp = MagicMock(return_value=None)
            manager._download_with_gallery_dl = MagicMock(return_value=None)
            app.media = manager

            telegram = PrivateReviewTelegramBot("token", 1, 99)
            telegram.api = MagicMock(
                return_value={
                    "video": {
                        "file_id": "fresh-v3-file-id",
                        "file_unique_id": "fresh-v3-unique-id",
                    }
                }
            )
            app.telegram = telegram

            v2_key = hashlib.sha256(
                f"telegram-ios-video-v2\n{item.kind}\n{item.url}".encode("utf-8")
            ).hexdigest()
            with app.media_cache.conn:
                app.media_cache.conn.execute(
                    "INSERT INTO telegram_media_cache(media_key,kind,original_url,file_id,file_unique_id) "
                    "VALUES(?,?,?,?,?)",
                    (
                        v2_key,
                        item.kind,
                        item.url,
                        "stale-v2-file-id",
                        "stale-v2-unique-id",
                    ),
                )

            try:
                self.assertIsNone(app.media_cache.get(item))
                self.assertTrue(asyncio.run(app._deliver_private_media(update)))

                self.assertEqual(download_calls, [item.url])
                manager._download_with_ytdlp.assert_not_called()
                manager._download_with_gallery_dl.assert_not_called()

                telegram.api.assert_called_once()
                call = telegram.api.call_args
                self.assertEqual(call.args[0], "sendVideo")
                self.assertEqual(call.kwargs["data"]["chat_id"], 99)
                self.assertEqual(call.kwargs["data"]["supports_streaming"], "true")
                self.assertGreater(int(call.kwargs["data"]["duration"]), 0)
                self.assertEqual(int(call.kwargs["data"]["width"]), 320)
                self.assertEqual(int(call.kwargs["data"]["height"]), 180)
                self.assertEqual(call.kwargs["data"]["thumbnail"], "attach://thumbnail")
                self.assertIn("video", call.kwargs["files"])
                self.assertIn("thumbnail", call.kwargs["files"])

                cached = app.media_cache.get(item)
                self.assertIsNotNone(cached)
                self.assertEqual(cached.file_id, "fresh-v3-file-id")
                self.assertEqual(cached.file_unique_id, "fresh-v3-unique-id")
                self.assertNotEqual(cached.media_key, v2_key)
            finally:
                app.media_delivery.close()
                app.media_cache.close()


if __name__ == "__main__":
    unittest.main()
