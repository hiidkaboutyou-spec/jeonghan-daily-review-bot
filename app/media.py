from __future__ import annotations

import importlib.util
import json
import logging
import math
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
SAFE_THUMBNAIL_BYTES = 190 * 1024
MAX_GALLERY_SECONDS = 120
TRUSTED_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
TRUSTED_YTDLP_HOSTS = {
    *TRUSTED_X_HOSTS,
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "instagram.com", "www.instagram.com",
    "reddit.com", "www.reddit.com", "old.reddit.com", "redd.it", "v.redd.it",
}


@dataclass(slots=True)
class PreparedMedia:
    kind: str
    path: Path
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    thumbnail_path: Path | None = None


class MediaManager:
    """Bounded media retrieval with X direct paths and trusted social extractors."""

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

    def _download_photo(self, item: MediaItem, post_url: str, root: Path, index: int) -> PreparedMedia | None:
        path = root / f"photo-{index}.jpg"
        last_error: Exception | None = None
        origin_missing = False
        # The X image CDN has useful explicit size variants. For other sources,
        # try the supplied media URL once and avoid mutating arbitrary query strings.
        if _is_x_media_url(item.url):
            high, lower = _photo_quality_candidates(item.url)
        else:
            high, lower = [("direct", item.url)], []

        for method, url in high:
            path.unlink(missing_ok=True)
            try:
                self._stream_download(url, path, SAFE_PHOTO_BYTES, attempts=2)
                if path.exists() and 0 < path.stat().st_size <= SAFE_PHOTO_BYTES:
                    content_type = mimetypes.guess_type(urlsplit(url).path)[0] or "image/jpeg"
                    if content_type not in {"image/jpeg", "image/png"}:
                        content_type = "image/jpeg"
                    return self._prepared("photo", path, content_type, method)
            except Exception as exc:
                last_error = exc
                logger.info("High-quality photo fallback needed (%s): %s", method, _safe_error(exc))
                if _missing_remote_media(exc):
                    origin_missing = True
                    break

        # gallery-dl remains an X-only metadata refresh. It is deliberately not
        # invoked for arbitrary user-controlled URLs.
        gallery = self._download_with_gallery_dl(
            post_url,
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

    def _download_video(self, item: MediaItem, post_url: str, root: Path, index: int) -> PreparedMedia | None:
        direct = root / f"video-{index}.mp4"
        downloaded: Path | None = None
        method = "direct"
        try:
            # Direct CDN media is cheapest and most deterministic. A webpage URL is
            # not a video file, so skip this attempt when the URL is clearly a page.
            if _looks_like_direct_media_url(item.url):
                self._stream_download(item.url, direct, SAFE_VIDEO_BYTES, attempts=2)
                if direct.exists() and 0 < direct.stat().st_size <= SAFE_VIDEO_BYTES:
                    downloaded = direct
        except Exception as exc:
            direct.unlink(missing_ok=True)
            logger.info("Direct video fallback needed: %s", _safe_error(exc))

        if downloaded is None:
            source_url = post_url if _trusted_ytdlp_url(post_url) else item.url
            downloaded = self._download_with_ytdlp(source_url, root, index)
            method = "yt-dlp"
            if downloaded is None:
                downloaded = self._download_with_gallery_dl(post_url, root, index, kind="video")
                method = "gallery-dl"
        if downloaded is None:
            return None

        finalized = self._finalize_video(downloaded, root / f"video-{index}-telegram.mp4")
        if finalized is None:
            logger.warning("Video could not be normalized into a playable Telegram MPEG-4 file.")
            return None
        downloaded, finalized_method = finalized
        method += finalized_method

        if downloaded.stat().st_size > SAFE_VIDEO_BYTES:
            compressed = self._compress_video(downloaded, root / f"video-{index}-compressed.mp4")
            if compressed is not None:
                downloaded = compressed
                method += "+transcode"
        if downloaded.stat().st_size > SAFE_VIDEO_BYTES:
            logger.warning("Video remains above Telegram safety limit after bounded fallbacks.")
            return None
        metadata = _probe_media(downloaded, kind="video")
        if not _telegram_video_compatible(metadata):
            logger.warning("Video normalization produced incomplete Telegram metadata.")
            return None
        return self._prepared("video", downloaded, "video/mp4", method)

    @staticmethod
    def _finalize_video(source: Path, target: Path) -> tuple[Path, str] | None:
        """Create a streamable MP4 whose timing and dimensions are probeable."""
        remuxed = target.with_name(target.stem + "-remux.mp4")
        remuxed.unlink(missing_ok=True)
        if _run_ffmpeg(
            [
                "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?",
                "-c", "copy",
                "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero",
                str(remuxed),
            ]
        ):
            metadata = _probe_media(remuxed, kind="video")
            if _telegram_video_compatible(metadata):
                return remuxed, "+remux"

        target.unlink(missing_ok=True)
        if not _run_ffmpeg(
            [
                "-i", str(source),
                "-map", "0:v:0", "-map", "0:a?",
                "-vf", r"scale=min(1280\,iw):-2,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-movflags", "+faststart",
                "-avoid_negative_ts", "make_zero",
                str(target),
            ]
        ):
            return None
        metadata = _probe_media(target, kind="video")
        if not _telegram_video_compatible(metadata):
            target.unlink(missing_ok=True)
            return None
        return target, "+transcode"

    def _prepared(self, kind: str, path: Path, content_type: str, method: str) -> PreparedMedia:
        metadata = _probe_media(path, kind=kind)
        metadata["retrieval_method"] = method
        metadata["filesize"] = path.stat().st_size if path.exists() else 0
        thumbnail = _make_video_thumbnail(path, metadata) if kind == "video" else None
        return PreparedMedia(
            kind=kind,
            path=path,
            content_type=content_type,
            metadata=metadata,
            thumbnail_path=thumbnail,
        )

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

    def _download_with_ytdlp(self, source_url: str, root: Path, index: int) -> Path | None:
        source_url = _normalize_social_url(source_url)
        if not _trusted_ytdlp_url(source_url):
            return None
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            return None
        cookie_file = root / "cookies.txt"
        use_x_cookies = _trusted_x_status_url(source_url) and bool(self.x_cookies)
        if use_x_cookies:
            _write_netscape_cookies(cookie_file, self.x_cookies)
        output = str(root / f"ytdlp-{index}.%(ext)s")
        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": output,
            "merge_output_format": "mp4",
            # Prefer H.264/AAC MP4 streams that Telegram/iOS can play directly,
            # then widen progressively instead of failing on one unavailable format.
            "format": (
                "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[acodec^=mp4a]/"
                "bestvideo[vcodec^=h264][ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "best[ext=mp4]/bestvideo+bestaudio/best"
            ),
            "socket_timeout": 45,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "cookiefile": str(cookie_file) if use_x_cookies else None,
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(source_url, download=True)
                filename = Path(ydl.prepare_filename(info))
                requested = info.get("requested_downloads") if isinstance(info, dict) else None
        except Exception as exc:
            logger.warning("yt-dlp failed for trusted social video: %s", _safe_error(exc))
            return None

        candidates: list[Path] = []
        if isinstance(requested, list):
            for entry in requested:
                if not isinstance(entry, dict):
                    continue
                filepath = entry.get("filepath") or entry.get("filename")
                if filepath:
                    candidates.append(Path(str(filepath)))
        candidates.extend([filename, filename.with_suffix(".mp4")])
        candidates.extend(sorted(root.glob(f"ytdlp-{index}.*")))
        # Post-processed/merged MP4 should win over stale component streams.
        candidates = sorted(
            dict.fromkeys(candidates),
            key=lambda path: (path.suffix.lower() == ".mp4", path.stat().st_mtime if path.exists() else 0),
            reverse=True,
        )
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
            return None

        videos = [path for path in files if path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}]
        if not videos:
            return None
        return max(videos, key=lambda path: path.stat().st_size)

    @staticmethod
    def _compress_video(source: Path, target: Path) -> Path | None:
        attempts = [
            ["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-vf", r"scale=min(1280\,iw):-2,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "-avoid_negative_ts", "make_zero", str(target)],
            ["-i", str(source), "-map", "0:v:0", "-map", "0:a?", "-vf", r"scale=min(960\,iw):-2,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-c:a", "aac", "-b:a", "80k", "-movflags", "+faststart", "-avoid_negative_ts", "make_zero", str(target)],
        ]
        for command in attempts:
            target.unlink(missing_ok=True)
            if (
                _run_ffmpeg(command)
                and target.exists()
                and 0 < target.stat().st_size <= SAFE_VIDEO_BYTES
                and _telegram_video_compatible(_probe_media(target, kind="video"))
            ):
                return target
        target.unlink(missing_ok=True)
        return None


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


def _normalize_social_url(url: str) -> str:
    value = str(url or "").strip()
    # Telegram text and copied captions often leave punctuation attached to URLs.
    # Strip only characters that cannot be part of the intended social URL ending.
    return value.rstrip(".,;:!?،؛؟)]}>\"'\u200f\u200e")


def _trusted_ytdlp_url(url: str) -> bool:
    try:
        parts = urlsplit(_normalize_social_url(url))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in TRUSTED_YTDLP_HOSTS:
        return False
    path = parts.path.lower()
    if host in TRUSTED_X_HOSTS:
        return "/status/" in path or "/i/web/status/" in path
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        return path in {"/watch", "/shorts", "/live"} or path.startswith(("/shorts/", "/live/"))
    if host == "youtu.be":
        return bool(path.strip("/"))
    if host in {"instagram.com", "www.instagram.com"}:
        return path.startswith(("/p/", "/reel/", "/reels/", "/tv/"))
    if host in {"reddit.com", "www.reddit.com", "old.reddit.com"}:
        return "/comments/" in path
    if host in {"redd.it", "v.redd.it"}:
        return bool(path.strip("/"))
    return False


def _trusted_x_status_url(url: str) -> bool:
    try:
        parts = urlsplit(_normalize_social_url(url))
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in TRUSTED_X_HOSTS:
        return False
    path = parts.path.lower()
    return "/status/" in path or "/i/web/status/" in path


def _is_x_media_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host.endswith("twimg.com")


def _looks_like_direct_media_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname:
        return False
    path = parts.path.lower()
    return path.endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv")) or "video.twimg.com" in (parts.hostname or "").lower()


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
        if video.get("codec_name"):
            metadata["video_codec"] = str(video["codec_name"]).lower()
        if video.get("pix_fmt"):
            metadata["pixel_format"] = str(video["pix_fmt"]).lower()
        if video.get("width") is not None:
            metadata["width"] = int(video["width"])
        if video.get("height") is not None:
            metadata["height"] = int(video["height"])
        if video.get("bit_rate"):
            try:
                metadata["video_bitrate"] = int(video["bit_rate"])
            except (TypeError, ValueError):
                pass
    audio = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"), None)
    metadata["audio_present"] = isinstance(audio, dict)
    if isinstance(audio, dict) and audio.get("codec_name"):
        metadata["audio_codec"] = str(audio["codec_name"]).lower()
    format_info = info.get("format") if isinstance(info, dict) else {}
    if isinstance(format_info, dict):
        if format_info.get("format_name"):
            metadata["container"] = str(format_info["format_name"])[:80]
        if format_info.get("bit_rate"):
            try:
                metadata.setdefault("video_bitrate", int(format_info["bit_rate"]))
            except (TypeError, ValueError):
                pass
    duration_value = format_info.get("duration") if isinstance(format_info, dict) else None
    if duration_value is None and isinstance(video, dict):
        duration_value = video.get("duration")
    try:
        exact_duration = float(duration_value)
    except (TypeError, ValueError):
        exact_duration = 0.0
    if math.isfinite(exact_duration) and exact_duration > 0:
        metadata["duration_exact"] = exact_duration
        metadata["duration"] = max(1, int(math.ceil(exact_duration)))
    return metadata


def _telegram_video_compatible(metadata: dict[str, Any]) -> bool:
    container = str(metadata.get("container") or "").lower()
    video_codec = str(metadata.get("video_codec") or "").lower()
    pixel_format = str(metadata.get("pixel_format") or "").lower()
    audio_present = bool(metadata.get("audio_present"))
    audio_codec = str(metadata.get("audio_codec") or "").lower()
    return (
        "mp4" in container
        and video_codec == "h264"
        and pixel_format in {"yuv420p", "yuvj420p"}
        and int(metadata.get("width") or 0) > 0
        and int(metadata.get("height") or 0) > 0
        and int(metadata.get("duration") or 0) > 0
        and (not audio_present or audio_codec == "aac")
    )


def _make_video_thumbnail(path: Path, metadata: dict[str, Any]) -> Path | None:
    if shutil.which("ffmpeg") is None or not _telegram_video_compatible(metadata):
        return None
    target = path.with_name(path.stem + "-thumbnail.jpg")
    duration = float(metadata.get("duration_exact") or metadata.get("duration") or 1)
    seek = min(1.0, max(0.05, duration * 0.1))
    for quality in (5, 8):
        target.unlink(missing_ok=True)
        if _run_ffmpeg(
            [
                "-ss", f"{seek:.3f}", "-i", str(path),
                "-frames:v", "1",
                "-vf", r"scale=min(320\,iw):-2",
                "-q:v", str(quality),
                str(target),
            ]
        ) and target.exists() and 0 < target.stat().st_size <= SAFE_THUMBNAIL_BYTES:
            return target
    target.unlink(missing_ok=True)
    return None


def _safe_error(exc: Exception) -> str:
    value = str(exc)
    value = re.sub(
        r"(?i)(auth_token|ct0|cookie|token|authorization|gemini_api_key)\s*[=:]\s*[^\s,;]+",
        r"\1=<redacted>",
        value,
    )
    return value[:500]
