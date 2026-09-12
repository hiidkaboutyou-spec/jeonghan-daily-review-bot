from __future__ import annotations

"""Last-resort read-only X timeline recovery through FxTwitter API v2.

This provider is deliberately used only when the authenticated X collector and
the existing public syndication endpoint are unavailable. It never becomes a
success-cursor authority; the caller still retains the normal full-success
cursor until authenticated collection can prove a complete window.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
import re
from urllib.parse import quote

import requests

from .models import MediaItem, Update, ensure_utc

FXTWITTER_STATUS_URL = "https://api.fxtwitter.com/2/profile/{handle}/statuses"
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


class FxTwitterError(RuntimeError):
    pass


@dataclass(slots=True)
class FxTwitterResult:
    updates: list[Update]
    raw_seen: int
    pages: int


def _media_items(status: dict[str, Any]) -> list[MediaItem]:
    media = status.get("media")
    if not isinstance(media, dict):
        return []

    raw_items = media.get("all")
    if not isinstance(raw_items, list):
        raw_items = []
        for key in ("photos", "videos"):
            values = media.get(key)
            if isinstance(values, list):
                raw_items.extend(values)

    result: list[MediaItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "photo").casefold()
        url = str(raw.get("url") or "")
        if kind == "mosaic_photo" and not url:
            formats = raw.get("formats")
            if isinstance(formats, dict):
                url = str(formats.get("jpeg") or formats.get("webp") or "")
            kind = "photo"
        preview = str(raw.get("thumbnail_url") or "")
        bitrate = 0
        if kind in {"video", "gif"}:
            formats = raw.get("formats")
            choices = [
                item
                for item in (formats if isinstance(formats, list) else [])
                if isinstance(item, dict) and str(item.get("url") or "").startswith("http")
            ]
            choices.sort(
                key=lambda item: (
                    int(item.get("bitrate", 0) or 0),
                    int(item.get("height", 0) or 0),
                ),
                reverse=True,
            )
            if choices:
                url = str(choices[0].get("url") or url)
                bitrate = int(choices[0].get("bitrate", 0) or 0)
        if not url:
            continue
        result.append(
            MediaItem(
                kind="animated_gif" if kind == "gif" else kind,
                url=url,
                preview_url=preview,
                bitrate=bitrate,
                width=int(raw.get("width", 0) or 0),
                height=int(raw.get("height", 0) or 0),
            )
        )
    return result


def _status_datetime(status: dict[str, Any]) -> datetime | None:
    created = status.get("created_at")
    if created:
        try:
            return ensure_utc(str(created))
        except (TypeError, ValueError, OverflowError):
            pass
    raw_timestamp = status.get("created_timestamp")
    try:
        if raw_timestamp is None:
            return None
        return ensure_utc(datetime.fromtimestamp(float(raw_timestamp), tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _parse_status(
    status: dict[str, Any],
    *,
    handle: str,
    start: datetime,
    end: datetime,
    include_replies: bool,
) -> Update | None:
    if str(status.get("type") or "status") != "status":
        return None
    if status.get("reposted_by"):
        return None

    author = status.get("author")
    if not isinstance(author, dict):
        return None
    screen_name = str(author.get("screen_name") or "").lstrip("@").strip()
    if screen_name.casefold() != handle.casefold():
        return None

    identifier = str(status.get("id") or "").strip()
    created_at = _status_datetime(status)
    if not identifier or created_at is None:
        return None
    lower = ensure_utc(start)
    upper = ensure_utc(end)
    if created_at < lower or created_at >= upper:
        return None

    replying_to = status.get("replying_to")
    reply_to_id = (
        str(replying_to.get("status") or "").strip()
        if isinstance(replying_to, dict)
        else ""
    )
    if reply_to_id and not include_replies:
        return None

    quote_status = status.get("quote")
    quoted_id = ""
    quoted_text = ""
    quoted_author = ""
    quoted_media: list[MediaItem] = []
    if isinstance(quote_status, dict) and str(quote_status.get("type") or "") == "status":
        quoted_id = str(quote_status.get("id") or "")
        quoted_text = str(quote_status.get("text") or "")
        quote_author = quote_status.get("author")
        if isinstance(quote_author, dict):
            quoted_author = str(quote_author.get("screen_name") or "").lstrip("@").strip()
        quoted_media = _media_items(quote_status)

    return Update(
        id=identifier,
        url=str(status.get("url") or f"https://x.com/{screen_name}/status/{identifier}"),
        author=screen_name,
        author_name=str(author.get("name") or screen_name),
        text=str(status.get("text") or "").strip(),
        created_at=created_at,
        conversation_id=identifier,
        reply_to_id=reply_to_id,
        quoted_id=quoted_id,
        quoted_text=quoted_text,
        quoted_author=quoted_author,
        quoted_media=quoted_media,
        lang=str(status.get("lang") or ""),
        media=_media_items(status),
        is_reply=bool(reply_to_id),
        raw_query=f"fxtwitter:@{handle}",
    )


def collect_fxtwitter_timeline(
    handle: str,
    start: datetime,
    end: datetime,
    *,
    include_replies: bool = True,
    timeout: tuple[float, float] = (5.0, 20.0),
    max_pages: int = 5,
) -> FxTwitterResult:
    normalized = str(handle or "").lstrip("@").strip()
    if not _HANDLE_RE.fullmatch(normalized):
        raise FxTwitterError("invalid X source handle")

    lower = ensure_utc(start)
    upper = ensure_utc(end)
    cursor = ""
    raw_seen = 0
    pages = 0
    updates: list[Update] = []

    for _ in range(max(1, int(max_pages))):
        params: dict[str, object] = {
            "count": 100,
            "with_replies": "true" if include_replies else "false",
        }
        if cursor:
            params["cursor"] = cursor
        else:
            params["since"] = max(0, int(lower.timestamp()) - 1)

        try:
            response = requests.get(
                FXTWITTER_STATUS_URL.format(handle=quote(normalized, safe="")),
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "jeonghan-review-bot/public-recovery",
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise FxTwitterError("FxTwitter request failed") from exc

        pages += 1
        if response.status_code == 204:
            break
        if response.status_code != 200:
            raise FxTwitterError(f"FxTwitter HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise FxTwitterError("FxTwitter returned malformed JSON") from exc
        if not isinstance(payload, dict) or int(payload.get("code", 0) or 0) != 200:
            raise FxTwitterError("FxTwitter timeline response was unsuccessful")

        results = payload.get("results")
        if not isinstance(results, list):
            raise FxTwitterError("FxTwitter timeline results were malformed")
        if not results:
            break

        oldest: datetime | None = None
        for raw in results:
            if not isinstance(raw, dict):
                continue
            raw_seen += 1
            created = _status_datetime(raw)
            if created is not None and (oldest is None or created < oldest):
                oldest = created
            parsed = _parse_status(
                raw,
                handle=normalized,
                start=lower,
                end=upper,
                include_replies=include_replies,
            )
            if parsed is not None:
                updates.append(parsed)

        if oldest is not None and oldest <= lower:
            break

        cursors = payload.get("cursor")
        bottom = str(cursors.get("bottom") or "") if isinstance(cursors, dict) else ""
        if not bottom or bottom == cursor:
            break
        cursor = bottom

    chosen = {item.id: item for item in updates}
    return FxTwitterResult(
        updates=sorted(chosen.values(), key=lambda item: (item.created_at, item.id)),
        raw_seen=raw_seen,
        pages=pages,
    )
