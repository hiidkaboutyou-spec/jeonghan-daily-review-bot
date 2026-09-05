from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Update
from .completeness_evidence import record_observation
from .observability import current_retrieval_attempt_id
from .raw_observation import RawObservation, RawObservationStore
from .x_client import XCollector

logger = logging.getLogger(__name__)
_INSTALLED = False
_FROM_RE = re.compile(r"(?<!\S)from:([A-Za-z0-9_]{1,15})(?=\s|$)", re.I)
_TIMELINE_RE = re.compile(r"timeline:@([A-Za-z0-9_]{1,15})", re.I)


def _text(value: object, limit: int = 20000) -> str:
    return str(value or "")[:limit]


def _tweet_id(tweet: Any) -> str:
    return _text(
        getattr(tweet, "id_str", "") or getattr(tweet, "id", ""),
        160,
    ).strip()


def _tweet_author(tweet: Any) -> str:
    user = getattr(tweet, "user", None)
    return _text(getattr(user, "username", ""), 80).lstrip("@").strip().casefold()


def _source_from_provenance(raw_query: str) -> str:
    value = str(raw_query or "")
    match = _TIMELINE_RE.search(value) or _FROM_RE.search(value)
    return match.group(1).casefold() if match else ""


def _configured_source(collector: XCollector, handle: str):
    gate = getattr(collector, "source_mode_gate", None)
    sources = getattr(gate, "sources", {})
    if not isinstance(sources, dict):
        return None
    source = sources.get(str(handle or "").lstrip("@").strip().casefold())
    if source is None or not bool(getattr(source, "enabled", False)):
        return None
    return source


def _public_payload_hash(tweet: Any) -> str:
    """Hash bounded public provider fields only; never serialize auth/session state."""
    user = getattr(tweet, "user", None)
    quoted = getattr(tweet, "quotedTweet", None)
    retweeted = getattr(tweet, "retweetedTweet", None)
    payload = {
        "id": _tweet_id(tweet),
        "author": _text(getattr(user, "username", ""), 80),
        "date": _text(getattr(tweet, "date", ""), 120),
        "rawContent": _text(getattr(tweet, "rawContent", ""), 20000),
        "lang": _text(getattr(tweet, "lang", ""), 24),
        "conversationId": _text(getattr(tweet, "conversationId", ""), 160),
        "inReplyToTweetId": _text(getattr(tweet, "inReplyToTweetId", ""), 160),
        "quotedId": _text(
            getattr(quoted, "id_str", "") or getattr(quoted, "id", ""), 160
        ),
        "retweetedId": _text(
            getattr(retweeted, "id_str", "") or getattr(retweeted, "id", ""), 160
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _created_at(tweet: Any, update: Update | None) -> str:
    if update is not None:
        return update.created_at.astimezone(timezone.utc).isoformat()
    value = getattr(tweet, "date", None)
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return _text(value, 120)


def _media_json(update: Update | None, field: str) -> str:
    if update is None:
        return "[]"
    values = getattr(update, field, []) or []
    safe = []
    for item in values:
        safe.append(
            {
                "kind": _text(getattr(item, "kind", ""), 32),
                "url": _text(getattr(item, "url", ""), 4000),
                "preview_url": _text(getattr(item, "preview_url", ""), 4000),
                "bitrate": int(getattr(item, "bitrate", 0) or 0),
                "width": int(getattr(item, "width", 0) or 0),
                "height": int(getattr(item, "height", 0) or 0),
                "duration_ms": int(getattr(item, "duration_ms", 0) or 0),
                "content_type": _text(getattr(item, "content_type", ""), 120),
            }
        )
    return json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _post_type(tweet: Any, update: Update | None) -> str:
    if getattr(tweet, "retweetedTweet", None) is not None:
        return "retweet"
    reply_id = (
        _text(getattr(tweet, "inReplyToTweetId", ""), 160)
        or (_text(update.reply_to_id, 160) if update is not None else "")
    )
    if reply_id:
        return "reply"
    if getattr(tweet, "quotedTweet", None) is not None or (
        update is not None and bool(update.quoted_id)
    ):
        return "quote"
    if update is not None and not update.text.strip() and (update.media or update.quoted_media):
        return "media_only"
    return "post"


def _build_observation(
    collector: XCollector,
    tweet: Any,
    *,
    raw_query: str,
    update: Update | None,
    status: str,
) -> RawObservation | None:
    update_author = update.author.casefold() if update is not None else ""
    handle = update_author or _tweet_author(tweet) or _source_from_provenance(raw_query)
    source = _configured_source(collector, handle)
    if source is None:
        return None

    external_post_id = update.id if update is not None else _tweet_id(tweet)
    provider_hash = _public_payload_hash(tweet)
    if not external_post_id:
        external_post_id = "synthetic:" + provider_hash[:32]

    text = update.text if update is not None else _text(getattr(tweet, "rawContent", ""))
    quoted_text = update.quoted_text if update is not None else ""
    quoted_id = update.quoted_id if update is not None else ""
    quoted_author = update.quoted_author if update is not None else ""
    conversation_id = update.conversation_id if update is not None else _text(
        getattr(tweet, "conversationId", ""), 160
    )
    reply_to_id = update.reply_to_id if update is not None else _text(
        getattr(tweet, "inReplyToTweetId", ""), 160
    )
    lang = update.lang if update is not None else _text(getattr(tweet, "lang", ""), 24)
    post_type = _post_type(tweet, update)
    mode = _text(getattr(getattr(source, "mode", ""), "value", getattr(source, "mode", "")), 40)

    return RawObservation(
        provider="x",
        external_post_id=external_post_id,
        source_handle=handle,
        source_mode=mode,
        created_at=_created_at(tweet, update),
        text=_text(text),
        conversation_id=_text(conversation_id, 160),
        reply_to_id=_text(reply_to_id, 160),
        quoted_id=_text(quoted_id, 160),
        quoted_text=_text(quoted_text),
        quoted_author=_text(quoted_author, 80),
        lang=_text(lang, 24),
        media_json=_media_json(update, "media"),
        quoted_media_json=_media_json(update, "quoted_media"),
        post_type=post_type,
        is_retweet=post_type == "retweet",
        is_reply=post_type == "reply",
        is_quote=post_type == "quote",
        is_media_only=post_type == "media_only",
        provenance=_text(raw_query, 1000),
        retrieval_attempt_id=_text(current_retrieval_attempt_id(), 120),
        provider_payload_hash=provider_hash,
        observation_status=status,
    )


def _store_for(collector: XCollector) -> RawObservationStore:
    existing = getattr(collector, "raw_observation_store", None)
    if isinstance(existing, RawObservationStore):
        return existing

    override = str(os.environ.get("RAW_OBSERVATION_DB_PATH", "") or "").strip()
    if override:
        path = Path(override)
    else:
        x_db = Path(getattr(collector, "db_path", Path(".state/x_accounts.db")))
        path = x_db.with_name("private-review.sqlite3")
    store = RawObservationStore(path)
    collector.raw_observation_store = store
    return store


def install() -> None:
    global _INSTALLED
    if _INSTALLED or XCollector.__dict__.get("_raw_observation_installed", False):
        _INSTALLED = True
        return

    original_convert = XCollector._convert_tweet

    def convert_tweet(self: XCollector, tweet: Any, *, raw_query: str) -> Update | None:
        try:
            update = original_convert(self, tweet, raw_query=raw_query)
        except Exception:
            observation = _build_observation(
                self,
                tweet,
                raw_query=raw_query,
                update=None,
                status="conversion_error",
            )
            if observation is not None:
                _store_for(self).record(observation)
                record_observation(observation.external_post_id)
            raise

        observation = _build_observation(
            self,
            tweet,
            raw_query=raw_query,
            update=update,
            status="converted" if update is not None else "conversion_failed",
        )
        if observation is not None:
            _store_for(self).record(observation)
            record_observation(observation.external_post_id)
        return update

    XCollector._convert_tweet = convert_tweet
    XCollector._raw_observation_installed = True
    _INSTALLED = True


install()
