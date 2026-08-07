from __future__ import annotations

import asyncio
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from app.media import (
    MediaManager,
    SAFE_VIDEO_BYTES,
    _photo_quality_candidates,
    _run_ffmpeg,
    _safe_error,
)
from app.models import MediaItem, Update
from app.private_runtime import PrivateReviewApplication


class HighQualityMediaTests(unittest.TestCase):
    def photo(self) -> MediaItem:
        return MediaItem(kind="photo", url="https://pbs.twimg.com/media/ABC123?format=jpg&name=medium")

    def update(self, media) -> Update:
        return Update(
            id="123",
            url="https://x.com/source/status/123",
            author="source",
            author_name="Source",
            text="JEONGHAN",
            created_at=datetime.now(timezone.utc),
            media=list(media),
        )

    def test_photo_quality_order_preserves_unique_direct_and_size_fallbacks(self):
        high, low = _photo_quality_candidates(self.photo().url)
        self.assertEqual([name for name, _ in high], ["x-direct", "x-orig", "x-4096"])
        # The direct URL is already the medium variant, so retrying the exact same
        # URL later would waste a request. The remaining unique lower fallbacks are
        # therefore large -> small; medium has already been attempted as x-direct.
        self.assertEqual([name for name, _ in low], ["x-large", "x-small"])
        urls = [url for _, url in high + low]
        self.assertEqual(len(urls), len(set(urls)))

    def test_original_direct_photo_success_skips_extractors(self):
        manager = MediaManager({})
        with tempfile.TemporaryDirectory() as temp:
            def download(url, path, limit, attempts=2):
                path.write_bytes(b"jpeg")
            manager._stream_download = MagicMock(side_effect=download)
            manager._download_with_gallery_dl = MagicMock()
            with patch("app.media._probe_media", return_value={}):
                result = manager._download_photo(self.photo(), "https://x.com/a/status/1", Path(temp), 0)
            self.assertIsNotNone(result)
            self.assertEqual(result.metadata["retrieval_method"], "x-direct")
            manager._download_with_gallery_dl.assert_not_called()

    def test_orig_failure_falls_to_4096_before_lower_sizes(self):
        manager = MediaManager({})
        with tempfile.TemporaryDirectory() as temp:
            seen = []
            def download(url, path, limit, attempts=2):
                seen.append(url)
                if "name=4096x4096" in url:
                    path.write_bytes(b"best-compatible")
                    return
                raise requests.ConnectionError("failed")
            manager._stream_download = MagicMock(side_effect=download)
            manager._download_with_gallery_dl = MagicMock(return_value=None)
            with patch("app.media._probe_media", return_value={}):
                result = manager._download_photo(self.photo(), "https://x.com/a/status/1", Path(temp), 0)
            self.assertIsNotNone(result)
            self.assertEqual(result.metadata["retrieval_method"], "x-4096")
            self.assertFalse(any("name=large" in value for value in seen))

    def test_gallery_dl_photo_fallback_precedes_lower_sizes(self):
        manager = MediaManager({})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gallery = root / "gallery.jpg"
            gallery.write_bytes(b"gallery-photo")
            manager._stream_download = MagicMock(side_effect=requests.ConnectionError("direct failed"))
            manager._download_with_gallery_dl = MagicMock(return_value=gallery)
            with patch("app.media._probe_media", return_value={}):
                result = manager._download_photo(self.photo(), "https://x.com/a/status/1", root, 0)
            self.assertIsNotNone(result)
            self.assertEqual(result.metadata["retrieval_method"], "gallery-dl")
            self.assertEqual(manager._stream_download.call_count, 3)

    def test_ytdlp_uses_best_video_audio_with_combined_fallback(self):
        captured = {}
        class FakeYDL:
            def __init__(self, options):
                captured.update(options)
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def extract_info(self, url, download=True):
                out = Path(captured["outtmpl"].replace("%(ext)s", "mp4"))
                out.write_bytes(b"video")
                return {"id": "123"}
            def prepare_filename(self, info):
                return captured["outtmpl"].replace("%(ext)s", "mp4")
        fake_module = types.ModuleType("yt_dlp")
        fake_module.YoutubeDL = FakeYDL
        manager = MediaManager({})
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"yt_dlp": fake_module}):
            result = manager._download_with_ytdlp("https://x.com/a/status/1", Path(temp), 0)
        self.assertIsNotNone(result)
        self.assertIn("bestvideo[ext=mp4]+bestaudio[ext=m4a]", captured["format"])
        self.assertIn("/best[ext=mp4]/best", captured["format"])
        self.assertEqual(captured["merge_output_format"], "mp4")

    def test_ytdlp_failure_uses_gallery_video_fallback(self):
        manager = MediaManager({})
        item = MediaItem(kind="video", url="https://video.twimg.com/ext_tw_video/1/pu/vid/a.mp4")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gallery = root / "fallback.mp4"
            gallery.write_bytes(b"v" * 100)
            manager._stream_download = MagicMock(side_effect=requests.ConnectionError("direct fail"))
            manager._download_with_ytdlp = MagicMock(return_value=None)
            manager._download_with_gallery_dl = MagicMock(return_value=gallery)
            with patch("app.media._probe_media", return_value={}):
                result = manager._download_video(item, "https://x.com/a/status/1", root, 0)
            self.assertIsNotNone(result)
            self.assertEqual(result.metadata["retrieval_method"], "gallery-dl")
            manager._download_with_gallery_dl.assert_called_once()

    def test_gallery_dl_unavailable_is_safe(self):
        manager = MediaManager({"auth_token": "secret"})
        with tempfile.TemporaryDirectory() as temp, patch("app.media.importlib.util.find_spec", return_value=None):
            self.assertIsNone(manager._download_with_gallery_dl(
                "https://x.com/a/status/1", Path(temp), 0, kind="video"
            ))

    def test_gallery_command_never_contains_cookie_value(self):
        manager = MediaManager({"auth_token": "VERY_PRIVATE_COOKIE", "ct0": "PRIVATE_CT0"})
        seen_command = []
        def fake_run(command, **kwargs):
            seen_command.extend(command)
            return types.SimpleNamespace(returncode=1, stderr="")
        with tempfile.TemporaryDirectory() as temp, \
             patch("app.media.importlib.util.find_spec", return_value=object()), \
             patch("app.media.subprocess.run", side_effect=fake_run):
            result = manager._download_with_gallery_dl(
                "https://x.com/a/status/1", Path(temp), 0, kind="video"
            )
        self.assertIsNone(result)
        rendered = " ".join(map(str, seen_command))
        self.assertNotIn("VERY_PRIVATE_COOKIE", rendered)
        self.assertNotIn("PRIVATE_CT0", rendered)

    def test_untrusted_url_never_reaches_gallery_dl(self):
        manager = MediaManager({})
        with tempfile.TemporaryDirectory() as temp, patch("app.media.subprocess.run") as run:
            result = manager._download_with_gallery_dl(
                "https://example.com/status/1", Path(temp), 0, kind="video"
            )
        self.assertIsNone(result)
        run.assert_not_called()

    def test_stream_download_retries_are_bounded(self):
        manager = MediaManager({})
        manager.session.get = MagicMock(side_effect=requests.ConnectionError("network"))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(requests.ConnectionError):
                manager._stream_download("https://pbs.twimg.com/media/x", Path(temp) / "x.jpg", 1024, attempts=2)
        self.assertEqual(manager.session.get.call_count, 2)

    def test_photo_fallback_has_no_infinite_loop_or_duplicate_url_attempt(self):
        manager = MediaManager({})
        manager._stream_download = MagicMock(side_effect=requests.ConnectionError("no"))
        manager._download_with_gallery_dl = MagicMock(return_value=None)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(requests.ConnectionError):
                manager._download_photo(self.photo(), "https://x.com/a/status/1", Path(temp), 0)
        # medium was already attempted as the direct URL, so it is not retried later.
        self.assertEqual(manager._stream_download.call_count, 5)
        attempted = [call.args[0] for call in manager._stream_download.call_args_list]
        self.assertEqual(len(attempted), len(set(attempted)))
        manager._download_with_gallery_dl.assert_called_once()

    def test_ffmpeg_unavailable_fails_cleanly(self):
        with patch("app.media.shutil.which", return_value=None):
            self.assertFalse(_run_ffmpeg(["-i", "in", "out"]))

    def test_safe_error_redacts_auth_values(self):
        text = _safe_error(RuntimeError("auth_token=SECRET ct0=ALSOSECRET authorization=BearerSecret"))
        self.assertNotIn("SECRET", text)
        self.assertNotIn("ALSOSECRET", text)
        self.assertNotIn("BearerSecret", text)

    def test_cached_file_id_bypasses_media_retrieval(self):
        app = PrivateReviewApplication.__new__(PrivateReviewApplication)
        app.media_cache = MagicMock()
        app.media_cache.get_all.return_value = [object()]
        app.telegram = MagicMock()
        app.media = MagicMock()
        update = self.update([self.photo()])
        asyncio.run(app._deliver_private_media(update))
        app.telegram.send_cached_media.assert_called_once()
        app.media.prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
