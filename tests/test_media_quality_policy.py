from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.media import MediaManager, _probe_media
from app.media_quality import (
    quality_rank,
    short_edge_resolution,
    x_variant_dimensions,
)
from app.models import MediaItem


class MediaQualityPolicyTests(unittest.TestCase):
    def test_x_variant_dimensions_parse_landscape_and_portrait_urls(self):
        self.assertEqual(
            x_variant_dimensions(
                "https://video.twimg.com/ext_tw_video/1/pu/vid/avc1/1920x1080/example.mp4"
            ),
            (1920, 1080),
        )
        self.assertEqual(
            x_variant_dimensions(
                "https://video.twimg.com/ext_tw_video/1/pu/vid/720x1280/example.mp4"
            ),
            (720, 1280),
        )

    def test_short_edge_treats_portrait_1080_as_1080p(self):
        self.assertEqual(short_edge_resolution(1920, 1080), 1080)
        self.assertEqual(short_edge_resolution(1080, 1920), 1080)
        self.assertEqual(short_edge_resolution(720, 1280), 720)

    def test_quality_rank_prefers_1080_then_720_then_highest_other_source_quality(self):
        variants = [
            MediaItem(kind="video", url="u360", width=640, height=360, bitrate=800_000),
            MediaItem(kind="video", url="u1440", width=2560, height=1440, bitrate=8_000_000),
            MediaItem(kind="video", url="u720", width=1280, height=720, bitrate=3_000_000),
            MediaItem(kind="video", url="u1080", width=1920, height=1080, bitrate=5_000_000),
        ]
        ordered = sorted(
            variants,
            key=lambda item: quality_rank(item.width, item.height, item.bitrate),
            reverse=True,
        )
        self.assertEqual([item.url for item in ordered], ["u1080", "u720", "u1440", "u360"])

    def test_quality_rank_uses_highest_available_when_1080_and_720_are_absent(self):
        variants = [
            MediaItem(kind="video", url="u480", width=854, height=480, bitrate=1_000_000),
            MediaItem(kind="video", url="u1440", width=2560, height=1440, bitrate=8_000_000),
            MediaItem(kind="video", url="u360", width=640, height=360, bitrate=700_000),
        ]
        ordered = sorted(
            variants,
            key=lambda item: quality_rank(item.width, item.height, item.bitrate),
            reverse=True,
        )
        self.assertEqual(ordered[0].url, "u1440")


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"),
    "FFmpeg and ffprobe are required for 1080p preservation coverage",
)
class MediaQualityFfmpegTests(unittest.TestCase):
    def test_normalization_preserves_1080p_source_without_upscaling_or_downscaling(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            target = root / "normalized.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=10",
                    "-t", "0.3",
                    "-c:v", "mpeg4",
                    "-q:v", "5",
                    str(source),
                ],
                check=True,
                timeout=30,
            )

            finalized = MediaManager._finalize_video(source, target)
            self.assertIsNotNone(finalized)
            path, _method = finalized
            metadata = _probe_media(path, kind="video")
            self.assertEqual(short_edge_resolution(metadata["width"], metadata["height"]), 1080)


if __name__ == "__main__":
    unittest.main()
