from __future__ import annotations

from contextlib import aclosing
from typing import Any

from . import phase3_recovery as _phase3
from .models import Update, ensure_utc
from .observability import observe
from .x_client import XCollectionError, normalize_handle

# Unresolved retrieval boundaries are correctness state, not cache hints. Keep them
# until successful completion (or an explicit future migration), while retaining a
# bounded number of checkpoints and a bounded amount of serialized post progress.
_phase3.MAX_CHECKPOINT_UPDATES = 5000


def _sanitize_checkpoint_without_age_expiry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    required_time_fields = ("window_start", "segment_start", "segment_end", "updated_at")
    if any(not isinstance(raw.get(key), str) or not str(raw.get(key)).strip() for key in required_time_fields):
        return None
    try:
        version = int(raw.get("version", 0))
        source = normalize_handle(str(raw.get("source", "")))
        checkpoint_id = str(raw.get("checkpoint_id", ""))[:40]
        include_replies = bool(raw.get("include_replies", True))
        window_start = ensure_utc(raw.get("window_start"))
        segment_start = ensure_utc(raw.get("segment_start"))
        segment_end = ensure_utc(raw.get("segment_end"))
        updated_at = ensure_utc(raw.get("updated_at"))
        pages_completed = max(0, int(raw.get("pages_completed", 0) or 0))
        raw_seen = max(0, int(raw.get("raw_seen", 0) or 0))
        retry_count = max(0, min(int(raw.get("retry_count", 0) or 0), 1000))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        version != _phase3.CHECKPOINT_VERSION
        or not source
        or not checkpoint_id
        or not (window_start <= segment_start < segment_end)
    ):
        return None
    if checkpoint_id != _phase3._checkpoint_id(source, window_start, include_replies):
        return None
    next_cursor_raw = raw.get("next_cursor")
    if next_cursor_raw is not None and (
        not isinstance(next_cursor_raw, str) or len(next_cursor_raw) > 4096
    ):
        return None
    next_cursor = str(next_cursor_raw or "") or None
    updates_raw = raw.get("updates", [])
    if not isinstance(updates_raw, list) or len(updates_raw) > _phase3.MAX_CHECKPOINT_UPDATES:
        return None
    updates: list[Update] = []
    try:
        for item in updates_raw:
            if not isinstance(item, dict):
                return None
            update = Update.from_dict(item)
            if update.author.casefold() != source.casefold():
                return None
            updates.append(update)
    except (TypeError, ValueError):
        return None
    return {
        "version": _phase3.CHECKPOINT_VERSION,
        "checkpoint_id": checkpoint_id,
        "source": source,
        "include_replies": include_replies,
        "window_start": window_start.isoformat(),
        "segment_start": segment_start.isoformat(),
        "segment_end": segment_end.isoformat(),
        "next_cursor": next_cursor,
        "pages_completed": pages_completed,
        "raw_seen": raw_seen,
        "retry_count": retry_count,
        "updates": _phase3._update_dicts(_phase3._dedupe_updates(updates)),
        "updated_at": updated_at.isoformat(),
    }


async def _lookup_user_with_scoped_id_recovery(api: Any, handle: str, attempt_id: str) -> Any:
    last_error: Exception | None = None
    for retry_count in range(_phase3.MAX_SOURCE_RETRIES + 1):
        try:
            user = await api.user_by_login(handle)
            if user is not None:
                if retry_count:
                    observe(
                        "source_retry",
                        stage="retrieval",
                        status="recovered",
                        source=handle,
                        retrieval_attempt_id=attempt_id,
                        retry_count=retry_count,
                        retry_outcome="profile_recovered",
                    )
                return user
            last_error = RuntimeError("profile lookup returned no user")
        except Exception as exc:
            last_error = exc
        if retry_count >= _phase3.MAX_SOURCE_RETRIES:
            break
        observe(
            "source_retry",
            level="warning",
            stage="retrieval",
            status="retrying",
            source=handle,
            retrieval_attempt_id=attempt_id,
            retry_count=retry_count + 1,
            retry_outcome="profile_retry",
        )
        await _phase3._sleep_for_retry(retry_count + 1)

    # The known production flamehanie failure occurs before page 1 because the
    # profile response cannot be parsed. Recover only the numeric user identity via
    # an exact configured-author search; this search is never treated as timeline
    # completeness. The direct author timeline must still resolve every range.
    search = getattr(api, "search", None)
    if callable(search):
        try:
            stream = search(f"from:{handle} -filter:retweets", limit=5)
            async with aclosing(stream) as results:
                async for tweet in results:
                    user = getattr(tweet, "user", None)
                    username = normalize_handle(str(getattr(user, "username", "")))
                    user_id = getattr(user, "id", None)
                    if username.casefold() != handle.casefold() or not user_id:
                        continue
                    observe(
                        "source_retry",
                        stage="retrieval",
                        status="recovered",
                        source=handle,
                        retrieval_attempt_id=attempt_id,
                        retry_count=_phase3.MAX_SOURCE_RETRIES,
                        retry_outcome="profile_id_scoped_search",
                    )
                    return user
        except Exception as exc:
            last_error = exc

    observe(
        "source_retry",
        level="error",
        stage="retrieval",
        status="failed",
        source=handle,
        retrieval_attempt_id=attempt_id,
        retry_count=_phase3.MAX_SOURCE_RETRIES,
        retry_outcome="profile_exhausted",
        error_class=type(last_error).__name__ if last_error else "RuntimeError",
    )
    raise XCollectionError(
        f"X profile lookup remained unavailable for @{handle} after bounded retries and scoped identity recovery."
    ) from last_error


# StateStore checkpoint helpers and the resumable timeline resolve these names from
# phase3_recovery at call time, so hardening them here upgrades the already-installed
# Phase 3 layer without creating a second state or observability system.
_phase3._sanitize_checkpoint = _sanitize_checkpoint_without_age_expiry
_phase3._lookup_user = _lookup_user_with_scoped_id_recovery
