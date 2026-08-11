from __future__ import annotations

import importlib.util
import json
import logging
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .models import MediaItem, Update

logger = logging.getLogger(__name__)
SAFE_VIDEO_BYTES = 44 * 1024 * 1024
SAFE_PHOTO_BYTES = 9 * 1024 * 1024
MAX_GALLERY_SECONDS = 120
TRUSTED_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}


@dataclass(slots=True)
class PreparedMedia:
    kind: str
    path: Path
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class MediaManager:
    """Existing direct X pipeline strengthened with bounded optional fallbacks."""

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
                    value = self._download_photo(item, update.url, root, index)
                elif item.kind == "video":
                    value = self._download_video(item, update.url, root, index)
                else:
                    value = None
                if value is not None:
                    prepared.append(value)
                    logger.info(
                        "Prepared %s using %s (%s bytes)",
                        value.kind,
                        value.metadata.get("retrieval_method", "unknown"),
                        value.metadata.get("filesize", 0),
                    )
            except Exception as exc:
                logger.warning("Media %s/%s failed: %s", update.id, index, _safe_error(exc))
        return temp, prepared

    def _download_photo(self, item: MediaItem, tweet_url: str, root: Path, index: int) -> PreparedMedia | None:
        path = root / f"photo-{index}.jpg"
        last_error: Exception | None = None
        origin_missing = False
        high, lower = _photo_quality_candidates(item.url)

        for method, url in high:
            path.unlink(missing_ok=True)
            try:
                self._stream_download(url, path, SAFE_PHOTO_BYTES, attempts=2)
                if path.exists() and 0 < path.stat().st_size <= SAFE_PHOTO_BYTES:
                    return self._prepared("photo", path, "image/jpeg", method)
            except Exception as exc:
                last_error = exc
                logger.info("High-quality photo fallback needed (%s): %s", method, _safe_error(exc))
                if _missing_remote_media(exc):
                    # Every X size uses the same media path. If the origin itself
                    # is gone, changing only name=orig/large/small cannot revive it.
                    # Give gallery-dl one chance to refresh the Tweet metadata,
                    # then stop instead of making ten more doomed HTTP requests.
                    origin_missing = True
                    break

        gallery = self._download_with_gallery_dl(
            tweet_url,
            root,
            index,
            kind="photo",
            expected_media_url=item.url,
        )
        if gallery is not None and gallery.exists() and 0 < gallery.stat().st_size <= SAFE_PHOTO_BYTES:
            content_type = mimetypes.guess_type(gallery.name)[0] or "image/jpeg"
            if content_type in {"image/jpeg", "image/png"}:
                return self._prepared("photo", gallery, content_type, "gallery-dl")

        if origin_missing and last_error is not None:
            raise last_error

        for method, url in lower:
            path.unlink(missing_ok=True)
            try:
                self._stream_download(url, path, SAFE_PHOTO_BYTES, attempts=2)
                if path.exists() and 0 < path.stat().st_size <= SAFE_PHOTO_BYTES:
                    return self._prepared("photo", path, "image/jpeg", method)
            except Exception as exc:
                last_error = exc
                logger.info("Photo size fallback needed (%s): %s", method, _safe_error(exc))
        if last_error is not None:
            raise last_error
        return None

    def _download_video(self, item: MediaItem, tweet_url: str, root: Path, index: int) -> PreparedMedia | None:
        direct = root / f"video-{index}.mp4"
        try:
            self._stream_download(item.url, direct, SAFE_VIDEO_BYTES, attempts=2)
            if direct.exists() and 0 < direct.stat().st_size <= SAFE_VIDEO_BYTES:
                return self._prepared("video", direct, "video/mp4", "x-direct")
        except Exception as exc:
            direct.unlink(missing_ok=True)
            logger.info("Direct X video fallback needed: %s", _safe_error(exc))

        downloaded = self._download_with_ytdlp(tweet_url, root, index)
        method = "yt-dlp"
        if downloaded is None:
            downloaded = self._download_with_gallery_dl(tweet_url, root, index, kind="video")
            method = "gallery-dl"
        if downloaded is None:
            return None

        if downloaded.suffix.lower() != ".mp4":
            remuxed = root / f"video-{index}-remux.mp4"
            if _run_ffmpeg(["-i", str(downloaded), "-c", "copy", "-movflags", "+faststart", str(remuxed)]):
                downloaded = remuxed
                method += "+remux"

        if downloaded.stat().st_size > SAFE_VIDEO_BYTES:
            compressed = self._compress_video(downloaded, root / f"video-{index}-compressed.mp4")
            if compressed is not None:
                downloaded = compressed
                method += "+transcode"
        if downloaded.stat().st_size > SAFE_VIDEO_BYTES:
            logger.warning("Video remains above Telegram safety limit after bounded fallbacks.")
            return None
        return self._prepared("video", downloaded, "video/mp4", method)

    def _prepared(self, kind: str, path: Path, content_type: str, method: str) -> PreparedMedia:
        metadata = _probe_media(path, kind=kind)
        metadata["retrieval_method"] = method
        metadata["filesize"] = path.stat().st_size if path.exists() else 0
        return PreparedMedia(kind=kind, path=path, content_type=content_type, metadata=metadata)

    def _stream_download(self, url: str, path: Path, max_bytes: int, *, attempts: int = 2) -> None:
        attempts = max(1, min(int(attempts), 3))
        last_error: Exception | None = None
        for attempt in range(attempts):
            path.unlink(missing_ok=True)
            try:
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
                return
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
        if last_error is not None:
            raise last_error

    def _download_with_ytdlp(self, tweet_url: str, root: Path, index: int) -> Path | None:
        if not _trusted_x_status_url(tweet_url):
            return None
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
            # Highest practical streams first, then a compatible combined-file fallback.
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best",
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
            logger.warning("yt-dlp failed for trusted X video: %s", _safe_error(exc))
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

    def _download_with_gallery_dl(
        self,
        tweet_url: str,
        root: Path,
        index: int,
        *,
        kind: str,
        expected_media_url: str = "",
    ) -> Path | None:
        """Optional single-Tweet extractor. Never accepts arbitrary/untrusted URLs."""
        if not _trusted_x_status_url(tweet_url):
            return None
        if importlib.util.find_spec("gallery_dl") is None:
            return None

        destination = root / f"gallery-{kind}-{index}"
        destination.mkdir(parents=True, exist_ok=True)
        config_path = root / f"gallery-{kind}-{index}.json"
        config = {
            "extractor": {
                "base-directory": str(destination),
                "directory": [],
                "filename": "{tweet_id}_{num}_{filename}.{extension}",
                "cookies": dict(self.x_cookies),
                "input": False,
                "twitter": {
                    "size": ["orig", "4096x4096", "large", "medium", "small"],
                    "videos": kind == "video",
                    "replies": False,
                    "retweets": False,
                    "quoted": False,
                    "conversations": False,
                    "expand": False,
                    "pinned": False,
                    "cards": False,
                    "logout": False,
                    "ratelimit": "abort:1",
                    "retries-api": 2,
                    "unique": False,
                },
            },
            "downloader": {"http": {"retries": 2, "timeout": 30}},
        }
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        try:
            config_path.chmod(0o600)
        except OSError:
            pass

        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--config-ignore",
            "--config",
            str(config_path),
            tweet_url,
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=MAX_GALLERY_SECONDS,
                check=False,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        if completed.returncode != 0:
            logger.info("Optional gallery-dl fallback exited with code %s", completed.returncode)
            return None

        files = [path for path in destination.rglob("*") if path.is_file()]
        if kind == "photo":
            photos = [path for path in files if path.suffix.lower() in {".jpg", ".jpeg", ".png"}]
            if not photos:
                return None
            expected = Path(urlsplit(expected_media_url).path).name.lower() if expected_media_url else ""
            matching = [path for path in photos if expected and expected in path.name.lower()]
            if matching:
                return max(matching, key=lambda path: path.stat().st_size)
            if len(photos) == 1:
                return photos[0]
            # Avoid returning the wrong photo from a multi-image Tweet.
            return None

        videos = [path for path in files if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
        if not videos:
            return None
        return max(videos, key=lambda path: path.stat().st_size)

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


def _photo_quality_candidates(url: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    fmt = params.get("format") or "jpg"

    def variant(name: str) -> str:
        query = dict(params)
        query["format"] = fmt
        query["name"] = name
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    high = [("x-direct", url), ("x-orig", variant("orig")), ("x-4096", variant("4096x4096"))]
    lower = [("x-large", variant("large")), ("x-medium", variant("medium")), ("x-small", variant("small"))]
    seen: set[str] = set()
    clean_high: list[tuple[str, str]] = []
    clean_lower: list[tuple[str, str]] = []
    for method, candidate in high:
        if candidate not in seen:
            seen.add(candidate)
            clean_high.append((method, candidate))
    for method, candidate in lower:
        if candidate not in seen:
            seen.add(candidate)
            clean_lower.append((method, candidate))
    return clean_high, clean_lower


def _missing_remote_media(exc: Exception) -> bool:
    if not isinstance(exc, requests.HTTPError):
        return False
    response = getattr(exc, "response", None)
    return int(getattr(response, "status_code", 0) or 0) in {404, 410}


def _photo_variants(url: str) -> list[str]:
    high, lower = _photo_quality_candidates(url)
    return [candidate for _, candidate in high + lower]


def _trusted_x_status_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in TRUSTED_X_HOSTS:
        return False
    path = parts.path.lower()
    return "/status/" in path or "/i/web/status/" in path


def _write_netscape_cookies(path: Path, cookies: dict[str, str]) -> None:
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in cookies.items():
        lines.append(f".x.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
        lines.append(f".twitter.com\tTRUE\t/\tTRUE\t2147483647\t{name}\t{value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _run_ffmpeg(arguments: list[str]) -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments]
    try:
        subprocess.run(command, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _probe_media(path: Path, *, kind: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"media_type": kind}
    if path.exists():
        metadata["filesize"] = path.stat().st_size
    if shutil.which("ffprobe") is None or not path.exists():
        return metadata
    try:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            check=True,
            timeout=15,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        info = json.loads(completed.stdout or "{}")
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return metadata
    streams = info.get("streams") if isinstance(info, dict) else []
    if not isinstance(streams, list):
        streams = []
    video = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), None)
    if isinstance(video, dict):
        if video.get("width") is not None:
            metadata["width"] = int(video["width"])
        if video.get("height") is not None:
            metadata["height"] = int(video["height"])
        if video.get("bit_rate"):
            try:
                metadata["video_bitrate"] = int(video["bit_rate"])
            except (TypeError, ValueError):
                pass
    metadata["audio_present"] = any(
        isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
    )
    format_info = info.get("format") if isinstance(info, dict) else {}
    if isinstance(format_info, dict):
        if format_info.get("format_name"):
            metadata["container"] = str(format_info["format_name"])[:80]
        if format_info.get("bit_rate"):
            try:
                metadata.setdefault("video_bitrate", int(format_info["bit_rate"]))
            except (TypeError, ValueError):
                pass
    return metadata


def _safe_error(exc: Exception) -> str:
    value = str(exc)
    value = re.sub(
        r"(?i)(auth_token|ct0|cookie|token|authorization|gemini_api_key)\s*[=:]\s*[^\s,;]+",
        r"\1=<redacted>",
        value,
    )
    return value[:500]
