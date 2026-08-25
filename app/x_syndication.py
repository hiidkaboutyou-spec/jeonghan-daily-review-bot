from __future__ import annotations

"""Read-only recovery collector for X's public profile syndication feed.

This is intentionally a partial-data fallback. The public feed is useful when the
authenticated web client cannot construct X transaction IDs, but it is not treated
as a complete timeline because X may omit replies or older posts.
"""

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from .models import MediaItem, Update, ensure_utc

SYNDICATION_URL = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}"
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', re.DOTALL
)
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


class SyndicationError(RuntimeError):
    pass


@dataclass(slots=True)
class SyndicationResult:
    updates: list[Update]
    raw_seen: int


def _expanded_text(tweet: dict[str, Any]) -> str:
    text = str(tweet.get("full_text") or tweet.get("text") or "")
    entities = tweet.get("entities") if isinstance(tweet.get("entities"), dict) else {}
    for entity in entities.get("urls", []) if isinstance(entities.get("urls"), list) else []:
        if not isinstance(entity, dict):
            continue
        short = str(entity.get("url") or "")
        expanded = str(entity.get("expanded_url") or short)
        if short and expanded:
            text = text.replace(short, expanded)
    return text.strip()


def _media_items(tweet: dict[str, Any]) -> list[MediaItem]:
    extended = tweet.get("extended_entities")
    if not isinstance(extended, dict):
        extended = tweet.get("entities") if isinstance(tweet.get("entities"), dict) else {}
    raw_media = extended.get("media", [])
    if not isinstance(raw_media, list):
        return []
    result: list[MediaItem] = []
    for raw in raw_media:
        if not isinstance(raw, dict):
            continue
        media_type = str(raw.get("type") or "photo").casefold()
        if media_type in {"video", "animated_gif"}:
            variants = (
                raw.get("video_info", {}).get("variants", [])
                if isinstance(raw.get("video_info"), dict)
                else []
            )
            choices = [
                item for item in variants
                if isinstance(item, dict) and str(item.get("url") or "").startswith("http")
            ]
            choices.sort(key=lambda item: int(item.get("bitrate", 0) or 0), reverse=True)
            url = str((choices[0] if choices else {}).get("url") or "")
            bitrate = int((choices[0] if choices else {}).get("bitrate", 0) or 0)
        else:
            url = str(raw.get("media_url_https") or raw.get("media_url") or "")
            bitrate = 0
        if not url:
            continue
        info = raw.get("original_info") if isinstance(raw.get("original_info"), dict) else {}
        result.append(
            MediaItem(
                kind=media_type,
                url=url,
                preview_url=str(raw.get("media_url_https") or ""),
                bitrate=bitrate,
                width=int(info.get("width", 0) or 0),
                height=int(info.get("height", 0) or 0),
            )
        )
    return result


def parse_syndication_html(
    document: str,
    *,
    handle: str,
    start: datetime,
    end: datetime,
    include_replies: bool = True,
) -> SyndicationResult:
    """Parse a public profile timeline and enforce source/window authority."""
    normalized = str(handle or "").lstrip("@").strip()
    if not _HANDLE_RE.fullmatch(normalized):
        raise SyndicationError("invalid X source handle")
    match = _NEXT_DATA_RE.search(str(document or ""))
    if match is None:
        raise SyndicationError("public X timeline did not contain structured data")
    try:
        encoded = match.group(1)
        try:
            payload = json.loads(encoded)
        except json.JSONDecodeError:
            payload = json.loads(html.unescape(encoded))
        entries = payload["props"]["pageProps"]["timeline"]["entries"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise SyndicationError("public X timeline data was malformed") from exc
    if not isinstance(entries, list):
        raise SyndicationError("public X timeline entries were malformed")

    lower = ensure_utc(start)
    upper = ensure_utc(end)
    updates: list[Update] = []
    raw_seen = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("type") != "tweet":
            continue
        content = entry.get("content")
        tweet = content.get("tweet") if isinstance(content, dict) else None
        if not isinstance(tweet, dict):
            continue
        raw_seen += 1
        user = tweet.get("user") if isinstance(tweet.get("user"), dict) else {}
        author = str(user.get("screen_name") or "").lstrip("@").strip()
        if author.casefold() != normalized.casefold():
            continue
        if tweet.get("retweeted_status") is not None:
            continue
        identifier = str(tweet.get("id_str") or "").strip()
        created_raw = tweet.get("created_at")
        if not identifier or not created_raw:
            continue
        try:
            created_at = ensure_utc(str(created_raw))
        except (TypeError, ValueError, OverflowError):
            continue
        if created_at < lower or created_at >= upper:
            continue
        reply_to = str(tweet.get("in_reply_to_status_id_str") or "")
        if reply_to and not include_replies:
            continue
        updates.append(
            Update(
                id=identifier,
                url=f"https://x.com/{author}/status/{identifier}",
                author=author,
                author_name=str(user.get("name") or author),
                text=_expanded_text(tweet),
                created_at=created_at,
                conversation_id=str(tweet.get("conversation_id_str") or identifier),
                reply_to_id=reply_to,
                lang=str(tweet.get("lang") or ""),
                media=_media_items(tweet),
                is_reply=bool(reply_to),
                raw_query=f"syndication:@{normalized}",
            )
        )
    chosen = {item.id: item for item in updates}
    return SyndicationResult(
        updates=sorted(chosen.values(), key=lambda item: (item.created_at, item.id)),
        raw_seen=raw_seen,
    )


def collect_syndication_timeline(
    handle: str,
    start: datetime,
    end: datetime,
    *,
    include_replies: bool = True,
    timeout: tuple[float, float] = (5.0, 15.0),
) -> SyndicationResult:
    normalized = str(handle or "").lstrip("@").strip()
    if not _HANDLE_RE.fullmatch(normalized):
        raise SyndicationError("invalid X source handle")
    try:
        response = requests.get(
            SYNDICATION_URL.format(handle=normalized),
            headers={"User-Agent": "jeonghan-review-bot/production-recovery"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SyndicationError("public X timeline request failed") from exc
    return parse_syndication_html(
        response.text,
        handle=normalized,
        start=start,
        end=end,
        include_replies=include_replies,
    )
