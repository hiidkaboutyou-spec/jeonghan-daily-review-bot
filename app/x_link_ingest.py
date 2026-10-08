from __future__ import annotations

"""Zero-friction ingestion for X links explicitly shared by the private admin.

This module does not participate in automatic timeline completeness and never advances
an X cursor. It turns a user-supplied public status URL into the existing Update model
through X's public syndication endpoint, so the normal media/translation/style/archive
pipeline can still be useful when authenticated timeline collection is degraded.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from .models import MediaItem, Update, ensure_utc
from .media_quality import quality_rank, x_variant_dimensions
from .x_fxtwitter import _parse_status as _parse_fxtwitter_status

SYNDICATION_STATUS_URL = "https://cdn.syndication.twimg.com/tweet-result"
FXTWITTER_SINGLE_STATUS_URL = "https://api.fxtwitter.com/2/status/{status_id}"
_URL_RE = re.compile(r"https?://[^\s<>]+", re.I)
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_STATUS_ID_RE = re.compile(r"^\d{5,30}$")
_ACCEPTED_HOSTS = {
    "x.com",
    "www.x.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "mobile.twitter.com",
    "fxtwitter.com",
    "www.fxtwitter.com",
    "fixupx.com",
    "www.fixupx.com",
    "vxtwitter.com",
    "www.vxtwitter.com",
}
_TRAILING_URL_PUNCTUATION = ".,!?;:،؛؟)]}>»”'\""
MAX_SHARED_LINKS = 8


class XLinkIngestError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SharedStatusRef:
    status_id: str
    expected_handle: str
    original_url: str


@dataclass(slots=True)
class LinkIngestResult:
    updates: list[Update]
    failed_ids: list[str]
    requested_count: int


def extract_status_links(text: str, *, limit: int = MAX_SHARED_LINKS) -> list[SharedStatusRef]:
    """Extract unique X status references without ever requesting user-provided hosts."""
    bounded = max(1, min(int(limit or MAX_SHARED_LINKS), MAX_SHARED_LINKS))
    refs: list[SharedStatusRef] = []
    seen: set[str] = set()
    for raw in _URL_RE.findall(str(text or "")):
        candidate = raw.rstrip(_TRAILING_URL_PUNCTUATION)
        try:
            parsed = urlparse(candidate)
        except ValueError:
            continue
        host = (parsed.hostname or "").casefold()
        if host not in _ACCEPTED_HOSTS:
            continue
        segments = [segment for segment in parsed.path.split("/") if segment]
        status_index = next(
            (index for index, segment in enumerate(segments) if segment.casefold() == "status"),
            -1,
        )
        if status_index < 0 or status_index + 1 >= len(segments):
            continue
        status_id = segments[status_index + 1].split("?", 1)[0]
        if not _STATUS_ID_RE.fullmatch(status_id) or status_id in seen:
            continue
        previous = segments[status_index - 1] if status_index > 0 else ""
        expected = previous if _HANDLE_RE.fullmatch(previous) and previous.casefold() not in {"i", "web"} else ""
        refs.append(
            SharedStatusRef(
                status_id=status_id,
                expected_handle=expected,
                original_url=candidate,
            )
        )
        seen.add(status_id)
        if len(refs) >= bounded:
            break
    return refs


def _syndication_token(status_id: str) -> str:
    """Match the token currently used by established syndication clients.

    yt-dlp is already a pinned project dependency. Keeping the import local prevents
    this manual convenience path from affecting normal startup if upstream ever moves
    the helper; token=0 is retained as the public endpoint's backwards-compatible
    fallback used by other current clients.
    """
    try:
        from yt_dlp.jsinterp import js_number_to_string

        raw = js_number_to_string((int(status_id) / 1e15) * math.pi, 36)
        token = raw.translate(str.maketrans("", "", "0."))
        return token or "0"
    except Exception:
        return "0"


def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _expanded_text(raw: dict[str, Any]) -> str:
    note = raw.get("note_tweet")
    note_text = ""
    if isinstance(note, dict):
        note_text = str(note.get("text") or "").strip()
        if not note_text:
            result = note.get("note_tweet_results")
            if isinstance(result, dict):
                result = result.get("result")
            if isinstance(result, dict):
                note_text = str(result.get("text") or "").strip()
    text = note_text or str(raw.get("full_text") or raw.get("text") or "").strip()
    entities = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    urls = entities.get("urls", []) if isinstance(entities.get("urls"), list) else []
    for entity in urls:
        if not isinstance(entity, dict):
            continue
        short = str(entity.get("url") or "")
        expanded = str(entity.get("expanded_url") or entity.get("display_url") or short)
        if short and expanded:
            text = text.replace(short, expanded)
    return text.strip()


def _media_items(raw: dict[str, Any]) -> list[MediaItem]:
    details = raw.get("mediaDetails")
    if not isinstance(details, list):
        details = []
    items: list[MediaItem] = []
    for media in details:
        if not isinstance(media, dict):
            continue
        kind = str(media.get("type") or "photo").casefold()
        preview = str(media.get("media_url_https") or media.get("media_url") or "")
        url = preview
        bitrate = 0
        if kind in {"video", "animated_gif"}:
            info = media.get("video_info") if isinstance(media.get("video_info"), dict) else {}
            variants = info.get("variants", []) if isinstance(info.get("variants"), list) else []
            choices: list[dict[str, Any]] = []
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                candidate = str(variant.get("url") or "")
                if not candidate.startswith(("https://", "http://")):
                    continue
                content_type = str(variant.get("content_type") or "")
                if content_type and not content_type.startswith("video/"):
                    continue
                choices.append(variant)
            choices.sort(
                key=lambda item: quality_rank(
                    *x_variant_dimensions(str(item.get("url") or "")),
                    _safe_nonnegative_int(item.get("bitrate")),
                ),
                reverse=True,
            )
            if choices:
                url = str(choices[0].get("url") or preview)
                bitrate = _safe_nonnegative_int(choices[0].get("bitrate"))
        if not url.startswith(("https://", "http://")):
            continue
        original = media.get("original_info") if isinstance(media.get("original_info"), dict) else {}
        items.append(
            MediaItem(
                kind=kind,
                url=url,
                preview_url=preview,
                bitrate=_safe_nonnegative_int(bitrate),
                width=_safe_nonnegative_int(original.get("width") or media.get("width")),
                height=_safe_nonnegative_int(original.get("height") or media.get("height")),
            )
        )

    # Some syndication responses expose photos outside mediaDetails.
    if not items and isinstance(raw.get("photos"), list):
        for photo in raw["photos"]:
            if not isinstance(photo, dict):
                continue
            url = str(photo.get("url") or photo.get("media_url_https") or "")
            if url.startswith(("https://", "http://")):
                items.append(
                    MediaItem(
                        kind="photo",
                        url=url,
                        preview_url=url,
                        width=_safe_nonnegative_int(photo.get("width")),
                        height=_safe_nonnegative_int(photo.get("height")),
                    )
                )
    return items


def _update_from_payload(payload: dict[str, Any], ref: SharedStatusRef) -> Update:
    identifier = str(payload.get("id_str") or payload.get("id") or "").strip()
    if identifier != ref.status_id:
        raise XLinkIngestError("X syndication returned a different status ID.")

    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    author = str(user.get("screen_name") or "").lstrip("@").strip()
    if not _HANDLE_RE.fullmatch(author):
        raise XLinkIngestError("X syndication response did not contain a valid author.")
    if ref.expected_handle and author.casefold() != ref.expected_handle.casefold():
        raise XLinkIngestError("X link author did not match the returned public status.")

    created_raw = payload.get("created_at")
    if not created_raw:
        raise XLinkIngestError("X syndication response did not contain a timestamp.")
    try:
        created_at = ensure_utc(str(created_raw))
    except (TypeError, ValueError, OverflowError) as exc:
        raise XLinkIngestError("X syndication returned an invalid timestamp.") from exc

    quoted = payload.get("quoted_tweet") if isinstance(payload.get("quoted_tweet"), dict) else {}
    quoted_user = quoted.get("user") if isinstance(quoted.get("user"), dict) else {}
    reply_to = str(payload.get("in_reply_to_status_id_str") or "")
    conversation_id = str(payload.get("conversation_id_str") or identifier)

    return Update(
        id=identifier,
        url=f"https://x.com/{author}/status/{identifier}",
        author=author,
        author_name=str(user.get("name") or author),
        text=_expanded_text(payload),
        created_at=created_at,
        conversation_id=conversation_id,
        reply_to_id=reply_to,
        quoted_id=str(quoted.get("id_str") or quoted.get("id") or ""),
        quoted_text=_expanded_text(quoted) if quoted else "",
        quoted_author=str(quoted_user.get("screen_name") or "").lstrip("@").strip(),
        quoted_media=_media_items(quoted) if quoted else [],
        lang=str(payload.get("lang") or ""),
        media=_media_items(payload),
        is_reply=bool(reply_to),
        raw_query="manual_link:x_syndication",
    )


def _fetch_fxtwitter_shared_status(ref: SharedStatusRef) -> Update:
    """Independent no-key public status fallback, never a timeline authority."""
    try:
        response = requests.get(
            FXTWITTER_SINGLE_STATUS_URL.format(status_id=ref.status_id),
            headers={
                "Accept": "application/json",
                "User-Agent": "jeonghan-daily-review-bot/manual-link-ingest",
            },
            timeout=(3.0, 8.0),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise XLinkIngestError("Both free public X status providers failed.") from exc
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise XLinkIngestError("FxTwitter did not return a valid public X status.")
    raw = payload.get("status")
    if not isinstance(raw, dict):
        raise XLinkIngestError("FxTwitter returned no usable public X status.")
    author = raw.get("author")
    handle = str(author.get("screen_name") or "").lstrip("@").strip() if isinstance(author, dict) else ""
    if not _HANDLE_RE.fullmatch(handle):
        raise XLinkIngestError("FxTwitter status author is invalid.")
    if ref.expected_handle and handle.casefold() != ref.expected_handle.casefold():
        raise XLinkIngestError("FxTwitter status author did not match the shared link.")
    try:
        update = _parse_fxtwitter_status(
            raw,
            handle=handle,
            start=datetime(2006, 1, 1, tzinfo=timezone.utc),
            end=datetime.now(timezone.utc) + timedelta(days=1),
            include_replies=True,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise XLinkIngestError("FxTwitter status metadata is malformed.") from exc
    if update is None or update.id != ref.status_id:
        raise XLinkIngestError("FxTwitter returned a different or invalid X post.")
    update.raw_query = "manual_link:fxtwitter"
    return update


def fetch_shared_status(ref: SharedStatusRef) -> Update:
    """Fetch a shared public post using bounded, independent keyless sources."""
    try:
        response = requests.get(
            SYNDICATION_STATUS_URL,
            params={"id": ref.status_id, "token": _syndication_token(ref.status_id)},
            headers={
                "Accept": "application/json",
                "User-Agent": "jeonghan-daily-review-bot/manual-link-ingest",
            },
            timeout=(5.0, 15.0),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return _fetch_fxtwitter_shared_status(ref)

    if not isinstance(payload, dict) or not payload:
        return _fetch_fxtwitter_shared_status(ref)
    try:
        return _update_from_payload(payload, ref)
    except XLinkIngestError:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise XLinkIngestError("X syndication returned malformed post metadata.") from exc


def collect_shared_statuses(text: str, *, limit: int = MAX_SHARED_LINKS) -> LinkIngestResult:
    refs = extract_status_links(text, limit=limit)
    updates: list[Update] = []
    failed: list[str] = []
    for ref in refs:
        try:
            updates.append(fetch_shared_status(ref))
        except XLinkIngestError:
            failed.append(ref.status_id)
    chosen = {item.id: item for item in updates if item.id}
    ordered = sorted(chosen.values(), key=lambda item: (item.created_at, item.id))
    return LinkIngestResult(
        updates=ordered,
        failed_ids=failed,
        requested_count=len(refs),
    )
