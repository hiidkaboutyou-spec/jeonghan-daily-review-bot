from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.media import MediaManager
from app.media_delivery import MediaDeliveryLedger
from app.media_delivery_runtime import MediaDedupReviewApplication
from app.media_file_cache import MediaFileCache
from app.models import MediaItem, Update
from app.private_telegram import PrivateReviewTelegramBot


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the isolated live video smoke")
    return value


def _make_source_video(path: Path) -> None:
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
            str(path),
        ],
        check=True,
        timeout=30,
    )


def main() -> int:
    token = _required_env("TELEGRAM_BOT_TOKEN")
    admin_id = int(_required_env("TELEGRAM_ADMIN_USER_ID"))
    review_chat_id = int(_required_env("TELEGRAM_REVIEW_CHAT_ID"))
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required")

    with tempfile.TemporaryDirectory(prefix="pr15-live-video-e2e-") as temp:
        root = Path(temp)
        db_path = root / "private-review.sqlite3"
        source = root / "source.mp4"
        _make_source_video(source)

        item = MediaItem(
            kind="video",
            url="https://video.twimg.com/ext_tw_video/1/pu/vid/pr15-live-e2e.mp4",
        )
        update = Update(
            id="pr15-live-video-e2e",
            url="https://x.com/i/web/status/1",
            author="pr15-e2e",
            author_name="PR #15 E2E",
            text="isolated video delivery smoke",
            created_at=datetime.now(timezone.utc),
            media=[item],
        )

        app = object.__new__(MediaDedupReviewApplication)
        app.media_cache = MediaFileCache(db_path)
        app.media_delivery = MediaDeliveryLedger(db_path)
        manager = MediaManager({})
        download_calls: list[str] = []

        def local_download(url: str, target: Path, max_bytes: int, *, attempts: int = 2) -> None:
            del max_bytes, attempts
            download_calls.append(url)
            shutil.copyfile(source, target)

        manager._stream_download = local_download
        app.media = manager
        telegram = PrivateReviewTelegramBot(
            token,
            admin_id,
            review_chat_id,
            send_pacing_seconds=0,
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
            if app.media_cache.get(item) is not None:
                raise RuntimeError("v2 cache entry unexpectedly survived the v3 lookup")
            if not asyncio.run(app._deliver_private_media(update)):
                raise RuntimeError("isolated video delivery returned false")
            if download_calls != [item.url]:
                raise RuntimeError("video was not re-retrieved exactly once after the v2 cache miss")

            cached = app.media_cache.get(item)
            if cached is None or not cached.file_id or not cached.file_unique_id:
                raise RuntimeError("Telegram success was not persisted into the v3 cache")
            if cached.media_key == v2_key:
                raise RuntimeError("fresh Telegram file_id was stored under the stale v2 key")

            remote = telegram.api("getFile", data={"file_id": cached.file_id}, timeout=30)
            if not isinstance(remote, dict) or not remote.get("file_path"):
                raise RuntimeError("Telegram getFile could not resolve the newly uploaded video")

            telegram.send_message(
                "✅ PR #15 live video delivery E2E passed: v2 cache was bypassed, the video was normalized and uploaded with sendVideo, and Telegram resolved the fresh file_id.",
                disable_preview=True,
            )
            print("LIVE_VIDEO_DELIVERY_E2E_OK")
            return 0
        finally:
            app.media_delivery.close()
            app.media_cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
