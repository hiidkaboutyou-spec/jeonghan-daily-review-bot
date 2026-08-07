from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .models import MediaItem, Update

logger = logging.getLogger(__name__)
SAFE_VIDEO_BYTES = 44 * 1024 * 1024
SAFE_PHOTO_BYTES = 9 * 1024 * 1024


@dataclass(slots=True)
class PreparedMedia:
    kind: str
    path: Path
    content_type: str


class MediaManager:
    def __init__(self, x_cookies: dict[str, str]):
        self.x_cookies = x_cookies
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/605.1.15 Safari/605.1.15",
                "Accept": "*/*",
            }
        )
        if x_cookies:
            self.session.cookies.update(x_cookies)

    def prepare(self, update: Update) -> tuple[tempfile.TemporaryDirectory, list[PreparedMedia]]:
        temp = tempfile.TemporaryDirectory(prefix=f"jeonghan-{update.id}-")
        root = Path(temp.name)
        prepared: list[PreparedMedia] = []
        for index, item in enumerate(update.media[:20]):
            try:
                if item.kind == "photo":
                    value = self._download_photo(item, root, index)
                elif item.kind == "video":
                    value = self._download_video(item, update.url, root, index)
                else:
                    value = None
                if value is not None:
                    prepared.append(value)
            except Exception as exc:
                logger.warning("Media %s/%s failed: %s", update.id, index, _safe_error(exc))
        return temp, prepared

    def _download_photo(self, item: MediaItem, root: Path, index: int) -> PreparedMedia | None:
        path = root / f"photo-{index}.jpg"
        last_error: Exception | None = None
        for url in _photo_variants(item.url):
            path.unlink(missing_ok=True)
            try:
                self._stream_download(url, path, SAFE_PHOTO_BYTES)
                if path.exists() and 0 < path.stat().st_size <= SAFE_PHOTO_BYTES:
                    return PreparedMedia(kind="photo", path=path, content_type="image/jpeg")
            except Exception as exc:
                last_error = exc
                logger.info("Photo variant fallback needed for %s: %s", url[-80:], _safe_error(exc))
        if last_error is not None:
            raise last_error
        return None

    def _download_video(self, item: MediaItem, tweet_url: str, root: Path, index: int) -> PreparedMedia | None:
        direct = root / f"video-{index}.mp4"
        try:
            self._stream_download(item.url, direct, SAFE_VIDEO_BYTES)
            if 0 < direct.stat().st_size <= SAFE_VIDEO_BYTES:
                return PreparedMedia(kind="video", path=direct, content_type="video/mp4")
        except Exception:
            direct.unlink(missing_ok=True)

        downloaded = self._download_with_ytdlp(tweet_url, root, index)
        if downloaded is None:
            return None
        if downloaded.stat().st_size > SAFE_VIDEO_BYTES:
            compressed = self._compress_video(downloaded, root / f"video-{index}-compressed.mp4")
            if compressed is not None:
                downloaded = compressed
        if downloaded.stat().st_size > SAFE_VIDEO_BYTES:
            logger.warning("Video remains above Telegram safety limit: %s", downloaded)
            return None
        return PreparedMedia(kind="video", path=downloaded, content_type="video/mp4")

    def _stream_download(self, url: str, path: Path, max_bytes: int) -> None:
        with self.session.get(url, stream=True, timeout=(20, 90), allow_redirects=True) as response:
            response.raise_for_status()
            length = int(response.headers.get("Content-Length", "0") or 0)
            if length and length > max_bytes:
                raise ValueError(f"Remote media is too large ({length} bytes).")
            total = 0
            with path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Downloaded media exceeded safe Telegram size.")
                    handle.write(chunk)

    def _download_with_ytdlp(self, tweet_url: str, root: Path, index: int) -> Path | None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return None
        cookie_file = root / "cookies.txt"
        _write_netscape_cookies(cookie_file, self.x_cookies)
        output = str(root / f"ytdlp-{index}.%(ext)s")
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": output,
            "merge_output_format": "mp4",
            "format": "best[ext=mp4][filesize<44M]/best[ext=mp4][filesize_approx<44M]/best[ext=mp4]/best",
            "socket_timeout": 45,
            "retries": 3,
            "fragment_retries": 3,
            "cookiefile": str(cookie_file) if self.x_cookies else None,
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(tweet_url, download=True)
                filename = Path(ydl.prepare_filename(info))
        except Exception as exc:
            logger.warning("yt-dlp failed for %s: %s", tweet_url, _safe_error(exc))
            return None
        candidates = [filename, filename.with_suffix(".mp4")] + sorted(root.glob(f"ytdlp-{index}.*"))
        for candidate in candidates:
            if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
                if candidate.suffix.lower() != ".mp4":
                    remuxed = root / f"ytdlp-{index}-remux.mp4"
                    if _run_ffmpeg(["-i", str(candidate), "-c", "copy", "-movflags", "+faststart", str(remuxed)]):
                        return remuxed
                return candidate
        return None

    @staticmethod
    def _compress_video(source: Path, target: Path) -> Path | None:
        attempts = [
            ["-i", str(source), "-vf", r"scale=min(1280\,iw):-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(target)],
            ["-i", str(source), "-vf", r"scale=min(960\,iw):-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-c:a", "aac", "-b:a", "80k", "-movflags", "+faststart", str(target)],
        ]
        for command in attempts:
            target.unlink(missing_ok=True)
            if _run_ffmpeg(command) and target.exists() and 0 < target.stat().st_size <= SAFE_VIDEO_BYTES:
                return target
        return target if target.exists() and target.stat().st_size > 0 else None


def _photo_variants(url: str) -> list[str]:
    """Prefer X original image, then gracefully fall back to Telegram-safe sizes."""
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    fmt = params.get("format") or "jpg"
    variants: list[str] = []
    for name in ("orig", "4096x4096", "large", "medium"):
        query = dict(params)
        query["format"] = fmt
        query["name"] = name
        variants.append(urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)))
    return list(dict.fromkeys(variants))


def _write_netscape_cookies(path: Path, cookies: dict[str, str]) -> None:
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in cookies.items():
        lines.append(f".x.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
        lines.append(f".twitter.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_ffmpeg(arguments: list[str]) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments]
    try:
        subprocess.run(command, check=True, timeout=180)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _safe_error(exc: Exception) -> str:
    value = str(exc)
    value = re.sub(r"(?i)(auth_token|ct0|cookie|token)=[^\s,;]+", r"\1=<redacted>", value)
    return value[:500]
