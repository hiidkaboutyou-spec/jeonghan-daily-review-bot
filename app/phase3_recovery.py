from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import observability as _observability
from . import state as _state_module
from . import source_authority_hardening as _source_authority
from .main import Application
from .models import Update, ensure_utc
from .completeness_evidence import active_evidence, record_page
from .observability import current_retrieval_attempt_id, new_attempt_id, observe
from .state import StateStore
from .x_client import XCollectionError, XCollector, _safe_error, normalize_handle
from .x_completeness import CompleteWindowXCollector, XCompletenessError
from .x_syndication import SyndicationError, collect_syndication_timeline

logger = logging.getLogger(__name__)

CHECKPOINT_VERSION = 1
MAX_SOURCE_RETRIES = 2
MAX_SYNDICATION_FALLBACKS_PER_WINDOW = 4
RETRY_DELAYS = (0.25, 0.75)
CHECKPOINT_TTL = timedelta(days=2)
CHECKPOINT_LIMIT = 48
MAX_CHECKPOINT_UPDATES = 1400

_observability._ALLOWED_TAGS.update(
    {
        "checkpoint_id",
        "page_index",
        "pages_completed",
        "first_unresolved_page",
        "retry_outcome",
        "resume",
    }
)


@dataclass(slots=True)
class _ProviderPage:
    tweets: list[Any]
    next_cursor: str | None
    exhausted: bool
    valid_response: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _configured_handle(self: XCollector, handle: str) -> bool:
    key = normalize_handle(handle).casefold()
    if not key:
        return False
    return any(
        item.get("enabled", True)
        and normalize_handle(str(item.get("handle", ""))).casefold() == key
        for item in self.sources
    )


def _checkpoint_id(handle: str, start: datetime, include_replies: bool) -> str:
    raw = f"{normalize_handle(handle).casefold()}|{ensure_utc(start).isoformat()}|{int(include_replies)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _update_dicts(updates: list[Update]) -> list[dict[str, Any]]:
    return [item.to_dict() for item in updates[-MAX_CHECKPOINT_UPDATES:]]


def _dedupe_updates(updates: list[Update]) -> list[Update]:
    chosen: dict[str, Update] = {}
    for item in updates:
        if not item.id:
            continue
        chosen[item.id] = item
    return sorted(chosen.values(), key=lambda item: (item.created_at, item.id))


def _sanitize_checkpoint(raw: Any) -> dict[str, Any] | None:
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
        version != CHECKPOINT_VERSION
        or not source
        or not checkpoint_id
        or not (window_start <= segment_start < segment_end)
        or updated_at < _now() - CHECKPOINT_TTL
    ):
        return None
    if checkpoint_id != _checkpoint_id(source, window_start, include_replies):
        return None
    next_cursor_raw = raw.get("next_cursor")
    if next_cursor_raw is not None and (
        not isinstance(next_cursor_raw, str) or len(next_cursor_raw) > 4096
    ):
        return None
    next_cursor = str(next_cursor_raw or "") or None
    updates_raw = raw.get("updates", [])
    if not isinstance(updates_raw, list) or len(updates_raw) > MAX_CHECKPOINT_UPDATES:
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
        "version": CHECKPOINT_VERSION,
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
        "updates": _update_dicts(_dedupe_updates(updates)),
        "updated_at": updated_at.isoformat(),
    }


def _install_state_checkpoints() -> None:
    if StateStore.__dict__.get("_phase3_checkpoint_installed", False):
        return

    _state_module.SCHEMA_VERSION = max(6, int(_state_module.SCHEMA_VERSION))
    original_fresh = StateStore._fresh
    original_normalize = StateStore._normalize_loaded
    original_prune = StateStore.prune

    def fresh(self):
        data = original_fresh(self)
        data.setdefault("x_retrieval_checkpoints", {})
        data["schema"] = _state_module.SCHEMA_VERSION
        return data

    def normalize(self, value):
        data = original_normalize(self, value)
        raw_checkpoints = value.get("x_retrieval_checkpoints") if isinstance(value, dict) else None
        clean: dict[str, dict[str, Any]] = {}
        invalid = 0
        if isinstance(raw_checkpoints, dict):
            for raw in list(raw_checkpoints.values())[-CHECKPOINT_LIMIT:]:
                checkpoint = _sanitize_checkpoint(raw)
                if checkpoint is None:
                    invalid += 1
                    continue
                clean[checkpoint["checkpoint_id"]] = checkpoint
        elif raw_checkpoints not in (None, {}):
            invalid += 1
        if invalid:
            logger.warning(
                "Discarded %s malformed X recovery checkpoint(s); affected ranges will be refetched conservatively.",
                invalid,
            )
        data["x_retrieval_checkpoints"] = clean
        data["schema"] = _state_module.SCHEMA_VERSION
        return data

    def prune(self):
        original_prune(self)
        raw = self.data.get("x_retrieval_checkpoints", {})
        if not isinstance(raw, dict):
            self.data["x_retrieval_checkpoints"] = {}
            logger.warning("Discarded malformed X recovery checkpoint container.")
            return
        clean: dict[str, dict[str, Any]] = {}
        for value in raw.values():
            checkpoint = _sanitize_checkpoint(value)
            if checkpoint is not None:
                clean[checkpoint["checkpoint_id"]] = checkpoint
        ordered = sorted(
            clean.items(),
            key=lambda pair: str(pair[1].get("updated_at", "")),
            reverse=True,
        )[:CHECKPOINT_LIMIT]
        self.data["x_retrieval_checkpoints"] = dict(ordered)

    def save_x_retrieval_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        clean = _sanitize_checkpoint(checkpoint)
        if clean is None:
            raise ValueError("Malformed X recovery checkpoint")
        self.data.setdefault("x_retrieval_checkpoints", {})[clean["checkpoint_id"]] = clean
        self.save()

    def clear_x_retrieval_checkpoint(self, checkpoint_id: str) -> None:
        self.data.setdefault("x_retrieval_checkpoints", {}).pop(str(checkpoint_id), None)
        self.save()

    def get_x_retrieval_checkpoint(
        self,
        *,
        source: str,
        start: datetime,
        end: datetime,
        include_replies: bool,
        allow_older: bool,
    ) -> dict[str, Any] | None:
        source_key = normalize_handle(source).casefold()
        start = ensure_utc(start)
        end = ensure_utc(end)
        exact_id = _checkpoint_id(source_key, start, include_replies)
        raw_map = self.data.get("x_retrieval_checkpoints", {})
        if not isinstance(raw_map, dict):
            logger.warning("Malformed X recovery checkpoint container; ignoring it conservatively.")
            return None

        candidates: list[dict[str, Any]] = []
        exact = _sanitize_checkpoint(raw_map.get(exact_id))
        if exact is not None:
            candidates.append(exact)
        if allow_older:
            for raw in raw_map.values():
                checkpoint = _sanitize_checkpoint(raw)
                if checkpoint is None or checkpoint in candidates:
                    continue
                if checkpoint["source"].casefold() != source_key:
                    continue
                if bool(checkpoint["include_replies"]) != bool(include_replies):
                    continue
                segment_end = ensure_utc(checkpoint["segment_end"])
                if segment_end <= end:
                    candidates.append(checkpoint)
        if not candidates:
            return None
        candidates.sort(key=lambda item: ensure_utc(item["window_start"]))
        return dict(candidates[0])

    StateStore._fresh = fresh
    StateStore._normalize_loaded = normalize
    StateStore.prune = prune
    StateStore.save_x_retrieval_checkpoint = save_x_retrieval_checkpoint
    StateStore.clear_x_retrieval_checkpoint = clear_x_retrieval_checkpoint
    StateStore.get_x_retrieval_checkpoint = get_x_retrieval_checkpoint
    StateStore._phase3_checkpoint_installed = True


async def _provider_page(
    api: Any,
    user_id: int,
    *,
    include_replies: bool,
    cursor: str | None,
) -> _ProviderPage:
    method_name = "user_tweets_and_replies_raw" if include_replies else "user_tweets_raw"
    method = getattr(api, method_name, None)
    if not callable(method):
        raise AttributeError(method_name)

    kwargs: dict[str, Any] = {"limit": 1}
    if cursor:
        kwargs["kv"] = {"cursor": cursor}

    response = None
    generator = method(user_id, **kwargs)
    async with aclosing(generator) as stream:
        async for item in stream:
            response = item
            break

    if response is None:
        return _ProviderPage([], None, True)

    payload = response.json()
    from twscrape.models import parse_tweets

    tweets = list(parse_tweets(payload, -1))
    get_cursor = getattr(api, "_get_cursor", None)
    if not callable(get_cursor):
        raise RuntimeError("provider cursor helper unavailable")
    next_cursor = get_cursor(payload, "Bottom")
    # A missing response or an error-shaped payload is not timeline exhaustion.
    # Retain legacy return behavior while giving shadow truth stronger evidence.
    def has_instructions(value):
        if isinstance(value, dict):
            return (value.get("type") == "TimelineAddEntries" and isinstance(value.get("entries"), list)
                    or any(has_instructions(child) for child in value.values()))
        if isinstance(value, list):
            return any(has_instructions(child) for child in value)
        return False

    valid = isinstance(payload, dict) and not payload.get("errors") and has_instructions(payload.get("data"))
    return _ProviderPage(tweets=tweets, next_cursor=str(next_cursor) if next_cursor else None, exhausted=not next_cursor, valid_response=valid)


async def _sleep_for_retry(retry_number: int) -> None:
    index = max(0, min(retry_number - 1, len(RETRY_DELAYS) - 1))
    await asyncio.sleep(RETRY_DELAYS[index])


async def _lookup_user(api: Any, handle: str, attempt_id: str) -> Any:
    last_error: Exception | None = None
    for retry_count in range(MAX_SOURCE_RETRIES + 1):
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
        if retry_count >= MAX_SOURCE_RETRIES:
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
        await _sleep_for_retry(retry_count + 1)

    observe(
        "source_retry",
        level="error",
        stage="retrieval",
        status="failed",
        source=handle,
        retrieval_attempt_id=attempt_id,
        retry_count=MAX_SOURCE_RETRIES,
        retry_outcome="profile_exhausted",
        error_class=type(last_error).__name__ if last_error else "RuntimeError",
    )
    raise XCollectionError(
        f"X profile lookup remained unavailable for @{handle} after bounded retries."
    ) from last_error


async def _fetch_page_with_retry(
    api: Any,
    user_id: int,
    *,
    handle: str,
    include_replies: bool,
    cursor: str | None,
    attempt_id: str,
    page_index: int,
) -> _ProviderPage:
    last_error: Exception | None = None
    for retry_count in range(MAX_SOURCE_RETRIES + 1):
        try:
            page = await _provider_page(
                api,
                user_id,
                include_replies=include_replies,
                cursor=cursor,
            )
            if retry_count:
                observe(
                    "source_retry",
                    stage="retrieval",
                    status="recovered",
                    source=handle,
                    retrieval_attempt_id=attempt_id,
                    page_index=page_index,
                    retry_count=retry_count,
                    retry_outcome="page_recovered",
                )
            return page
        except Exception as exc:
            last_error = exc
        if retry_count >= MAX_SOURCE_RETRIES:
            break
        observe(
            "source_retry",
            level="warning",
            stage="retrieval",
            status="retrying",
            source=handle,
            retrieval_attempt_id=attempt_id,
            page_index=page_index,
            retry_count=retry_count + 1,
            retry_outcome="page_retry",
            error_class=type(last_error).__name__ if last_error else "Error",
        )
        await _sleep_for_retry(retry_count + 1)

    observe(
        "source_retry",
        level="error",
        stage="retrieval",
        status="failed",
        source=handle,
        retrieval_attempt_id=attempt_id,
        page_index=page_index,
        retry_count=MAX_SOURCE_RETRIES,
        retry_outcome="page_exhausted",
        error_class=type(last_error).__name__ if last_error else "Error",
    )
    raise XCollectionError(
        f"X timeline page {page_index} failed for @{handle} after bounded retries."
    ) from last_error


def _new_checkpoint(
    handle: str,
    start: datetime,
    end: datetime,
    *,
    include_replies: bool,
) -> dict[str, Any]:
    return {
        "version": CHECKPOINT_VERSION,
        "checkpoint_id": _checkpoint_id(handle, start, include_replies),
        "source": normalize_handle(handle),
        "include_replies": bool(include_replies),
        "window_start": ensure_utc(start).isoformat(),
        "segment_start": ensure_utc(start).isoformat(),
        "segment_end": ensure_utc(end).isoformat(),
        "next_cursor": None,
        "pages_completed": 0,
        "raw_seen": 0,
        "retry_count": 0,
        "updates": [],
        "updated_at": _now().isoformat(),
    }


def _checkpoint_updates(checkpoint: dict[str, Any]) -> list[Update]:
    updates: list[Update] = []
    for raw in checkpoint.get("updates", []):
        if not isinstance(raw, dict):
            raise ValueError("Malformed checkpoint update")
        updates.append(Update.from_dict(raw))
    return updates


def _persist_checkpoint(
    state: StateStore | None,
    checkpoint: dict[str, Any],
    updates: list[Update],
) -> None:
    if state is None:
        return
    checkpoint["updates"] = _update_dicts(_dedupe_updates(updates))
    checkpoint["updated_at"] = _now().isoformat()
    state.save_x_retrieval_checkpoint(checkpoint)


def _clear_checkpoint(state: StateStore | None, checkpoint_id: str) -> None:
    if state is not None:
        state.clear_x_retrieval_checkpoint(checkpoint_id)


def _remember_partial(self: XCollector, handle: str, updates: list[Update]) -> None:
    current = getattr(self, "_phase3_partial_updates", None)
    if not isinstance(current, dict):
        current = {}
        self._phase3_partial_updates = current
    current[normalize_handle(handle).casefold()] = _dedupe_updates(updates)


_LEGACY_TIMELINE = CompleteWindowXCollector._collect_source_timeline


async def _resumable_source_timeline(
    self: XCollector,
    handle: str,
    start,
    end,
    *,
    limit: int,
    include_replies: bool,
) -> list[Update]:
    handle = normalize_handle(handle)
    if not handle:
        raise XCollectionError("Source handle is invalid.")
    if not _configured_handle(self, handle):
        raise XCollectionError(f"@{handle} is not a configured source.")

    api = await self._get_api()
    raw_method_name = "user_tweets_and_replies_raw" if include_replies else "user_tweets_raw"
    if not callable(getattr(api, raw_method_name, None)):
        return await _LEGACY_TIMELINE(
            self,
            handle,
            start,
            end,
            limit=limit,
            include_replies=include_replies,
        )

    start = ensure_utc(start)
    end = ensure_utc(end)
    limit = max(1, int(limit))
    attempt_id = current_retrieval_attempt_id() or new_attempt_id()
    state = getattr(self, "_phase3_state", None)
    if not isinstance(state, StateStore):
        state = None

    checkpoint = None
    if state is not None:
        checkpoint = state.get_x_retrieval_checkpoint(
            source=handle,
            start=start,
            end=end,
            include_replies=include_replies,
            allow_older=bool(getattr(self, "_phase3_allow_older_checkpoint", False)),
        )

    if checkpoint is None:
        checkpoint = _new_checkpoint(
            handle,
            start,
            end,
            include_replies=include_replies,
        )
        accumulated: list[Update] = []
        resumed = False
    else:
        try:
            checkpoint = _sanitize_checkpoint(checkpoint)
            if checkpoint is None:
                raise ValueError("checkpoint failed validation")
            accumulated = _checkpoint_updates(checkpoint)
        except (TypeError, ValueError):
            logger.warning("Malformed X checkpoint for @%s; refetching conservatively.", handle)
            checkpoint = _new_checkpoint(
                handle,
                start,
                end,
                include_replies=include_replies,
            )
            accumulated = []
            resumed = False
        else:
            resumed = True

    checkpoint_id = str(checkpoint["checkpoint_id"])
    segment_start = ensure_utc(checkpoint["segment_start"])
    segment_end = ensure_utc(checkpoint["segment_end"])
    if end < segment_end:
        checkpoint = _new_checkpoint(
            handle,
            start,
            end,
            include_replies=include_replies,
        )
        checkpoint_id = str(checkpoint["checkpoint_id"])
        segment_start = start
        segment_end = end
        accumulated = []
        resumed = False

    observe(
        "source_fetch_start",
        stage="retrieval",
        status="resuming" if resumed else "started",
        source=handle,
        retrieval_attempt_id=attempt_id,
        checkpoint_id=checkpoint_id,
        include_replies=include_replies,
        pagination="provider_cursor",
        pages_requested=limit,
        cursor_requested=bool(checkpoint.get("next_cursor")),
        pages_completed=int(checkpoint.get("pages_completed", 0) or 0),
        resume=resumed,
    )

    cursor = str(checkpoint.get("next_cursor") or "") or None
    seen_cursors: set[str] = set()
    run_raw_seen = 0
    crossed_lower_boundary = False

    try:
        user = await _lookup_user(api, handle, attempt_id)
        while True:
            page_index = int(checkpoint.get("pages_completed", 0) or 0) + 1
            cursor_before = cursor
            page = await _fetch_page_with_retry(
                api,
                user.id,
                handle=handle,
                include_replies=include_replies,
                cursor=cursor,
                attempt_id=attempt_id,
                page_index=page_index,
            )
            record_page(count=len(page.tweets), cursor=page.next_cursor, valid=page.valid_response)

            for tweet in page.tweets:
                run_raw_seen += 1
                checkpoint["raw_seen"] = int(checkpoint.get("raw_seen", 0) or 0) + 1
                update = self._convert_tweet(tweet, raw_query=f"timeline:@{handle}")
                if update is None:
                    continue
                if update.author.casefold() != handle.casefold():
                    observe(
                        "retrieval_filter_dedupe",
                        level="warning",
                        stage="filter_dedupe",
                        status="blocked_external_author",
                        source=handle,
                        retrieval_attempt_id=attempt_id,
                        external_dropped=1,
                        retained=0,
                    )
                    continue
                if update.created_at < segment_start:
                    crossed_lower_boundary = True
                    break
                if getattr(tweet, "retweetedTweet", None) is not None:
                    continue
                if update.created_at < segment_end:
                    accumulated.append(update)

            checkpoint["pages_completed"] = page_index
            checkpoint["retry_count"] = 0
            next_cursor = page.next_cursor

            if crossed_lower_boundary or page.exhausted or not next_cursor:
                checkpoint["next_cursor"] = None
                accumulated = _dedupe_updates(accumulated)
                if end > segment_end:
                    checkpoint["segment_start"] = segment_end.isoformat()
                    checkpoint["segment_end"] = end.isoformat()
                    checkpoint["next_cursor"] = None
                    checkpoint["raw_seen"] = 0
                    checkpoint["pages_completed"] = 0
                    segment_start = segment_end
                    segment_end = end
                    cursor = None
                    crossed_lower_boundary = False
                    _persist_checkpoint(state, checkpoint, accumulated)
                    continue

                _clear_checkpoint(state, checkpoint_id)
                evidence = active_evidence.get()
                if evidence is not None:
                    evidence.resumed = resumed
                    evidence.lower_boundary = crossed_lower_boundary
                    evidence.exhausted = bool(page.valid_response and not next_cursor)
                result = _dedupe_updates(accumulated)
                observe(
                    "source_fetch_end",
                    stage="retrieval",
                    status="complete",
                    source=handle,
                    retrieval_attempt_id=attempt_id,
                    checkpoint_id=checkpoint_id,
                    raw_seen=run_raw_seen,
                    retained=len(result),
                    cutoff_crossed=crossed_lower_boundary,
                    provider_exhausted=page.exhausted or not next_cursor,
                    pages_completed=int(checkpoint.get("pages_completed", 0) or 0),
                    complete=True,
                    partial=False,
                    retry_outcome="recovered" if resumed else "not_needed",
                )
                return result

            checkpoint["next_cursor"] = next_cursor
            accumulated = _dedupe_updates(accumulated)
            _persist_checkpoint(state, checkpoint, accumulated)

            if next_cursor == cursor_before or next_cursor in seen_cursors:
                checkpoint["next_cursor"] = cursor_before
                checkpoint["retry_count"] = int(checkpoint.get("retry_count", 0) or 0) + 1
                _persist_checkpoint(state, checkpoint, accumulated)
                _remember_partial(self, handle, accumulated)
                observe(
                    "source_fetch_end",
                    level="warning",
                    stage="retrieval",
                    status="partial_source_window",
                    source=handle,
                    retrieval_attempt_id=attempt_id,
                    checkpoint_id=checkpoint_id,
                    pages_completed=int(checkpoint.get("pages_completed", 0) or 0),
                    first_unresolved_page=page_index + 1,
                    raw_seen=run_raw_seen,
                    retained=len(accumulated),
                    complete=False,
                    partial=True,
                    error_class="DuplicateCursor",
                )
                raise XCompletenessError(
                    f"X timeline pagination stalled for @{handle}; checkpoint retained."
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor

            if run_raw_seen >= limit:
                _remember_partial(self, handle, accumulated)
                observe(
                    "source_fetch_end",
                    level="warning",
                    stage="retrieval",
                    status="partial_source_window",
                    source=handle,
                    retrieval_attempt_id=attempt_id,
                    checkpoint_id=checkpoint_id,
                    pages_completed=int(checkpoint.get("pages_completed", 0) or 0),
                    first_unresolved_page=page_index + 1,
                    raw_seen=run_raw_seen,
                    retained=len(accumulated),
                    cutoff_crossed=False,
                    provider_exhausted=False,
                    complete=False,
                    partial=True,
                    retry_outcome="checkpointed",
                    error_class="XCompletenessError",
                )
                raise XCompletenessError(
                    f"X timeline for @{handle} reached the per-run retrieval budget before the lower boundary; checkpoint retained."
                )
    except XCompletenessError:
        raise
    except XCollectionError as exc:
        fallback_error: Exception | None = None
        try:
            fallback_count = int(getattr(self, "_phase3_syndication_fallback_count", 0) or 0)
            if fallback_count >= MAX_SYNDICATION_FALLBACKS_PER_WINDOW:
                raise SyndicationError("public X fallback budget exhausted for this window")
            self._phase3_syndication_fallback_count = fallback_count + 1
            recovered = await asyncio.to_thread(
                collect_syndication_timeline,
                handle,
                segment_start,
                end,
                include_replies=include_replies,
            )
            accumulated = _dedupe_updates([*accumulated, *recovered.updates])
            _remember_partial(self, handle, accumulated)
            observe(
                "source_fallback",
                level="warning",
                stage="retrieval",
                status="recovered_partial",
                source=handle,
                retrieval_attempt_id=attempt_id,
                raw_seen=recovered.raw_seen,
                retained=len(recovered.updates),
                complete=False,
                partial=True,
                error_class=type(exc).__name__,
            )
        except (SyndicationError, OSError, ValueError) as recovery_exc:
            fallback_error = recovery_exc
            observe(
                "source_fallback",
                level="error",
                stage="retrieval",
                status="failed",
                source=handle,
                retrieval_attempt_id=attempt_id,
                retained=0,
                complete=False,
                partial=True,
                error_class=type(recovery_exc).__name__,
            )
        checkpoint["next_cursor"] = cursor
        checkpoint["retry_count"] = int(checkpoint.get("retry_count", 0) or 0) + 1
        _persist_checkpoint(state, checkpoint, accumulated)
        _remember_partial(self, handle, accumulated)
        observe(
            "source_fetch_end",
            level="error",
            stage="retrieval",
            status="partial_source_window",
            source=handle,
            retrieval_attempt_id=attempt_id,
            checkpoint_id=checkpoint_id,
            pages_completed=int(checkpoint.get("pages_completed", 0) or 0),
            first_unresolved_page=int(checkpoint.get("pages_completed", 0) or 0) + 1,
            raw_seen=run_raw_seen,
            retained=len(accumulated),
            complete=False,
            partial=True,
            retry_count=int(checkpoint.get("retry_count", 0) or 0),
            retry_outcome="exhausted",
            error_class=type(fallback_error or exc).__name__,
        )
        raise
    except Exception as exc:
        checkpoint["next_cursor"] = cursor
        checkpoint["retry_count"] = int(checkpoint.get("retry_count", 0) or 0) + 1
        _persist_checkpoint(state, checkpoint, accumulated)
        _remember_partial(self, handle, accumulated)
        observe(
            "source_fetch_end",
            level="error",
            stage="retrieval",
            status="partial_source_window",
            source=handle,
            retrieval_attempt_id=attempt_id,
            checkpoint_id=checkpoint_id,
            pages_completed=int(checkpoint.get("pages_completed", 0) or 0),
            first_unresolved_page=int(checkpoint.get("pages_completed", 0) or 0) + 1,
            raw_seen=run_raw_seen,
            retained=len(accumulated),
            complete=False,
            partial=True,
            retry_count=int(checkpoint.get("retry_count", 0) or 0),
            retry_outcome="exhausted",
            error_class=type(exc).__name__,
        )
        raise XCollectionError(
            f"X resumable timeline failed for @{handle}: {_safe_error(exc)}"
        ) from exc


_ORIGINAL_COLLECT_WINDOW = XCollector.collect_window


async def _resumable_collect_window(self: XCollector, *args, **kwargs) -> list[Update]:
    previous_flag = bool(getattr(self, "_phase3_allow_older_checkpoint", False))
    previous_partial = getattr(self, "_phase3_partial_updates", None)
    previous_fallback_count = int(
        getattr(self, "_phase3_syndication_fallback_count", 0) or 0
    )
    self._phase3_allow_older_checkpoint = True
    self._phase3_partial_updates = {}
    self._phase3_syndication_fallback_count = 0
    try:
        try:
            result = await _ORIGINAL_COLLECT_WINDOW(self, *args, **kwargs)
        except XCollectionError:
            partial_map = getattr(self, "_phase3_partial_updates", {})
            recovered = [
                item
                for updates in partial_map.values()
                if isinstance(updates, list)
                for item in updates
                if isinstance(item, Update)
            ] if isinstance(partial_map, dict) else []
            recovered = _source_authority._configured_only(self, recovered)
            if not recovered:
                raise
            logger.warning(
                "Core X collection failed, but %s source-authorized public fallback update(s) were recovered; cursor remains retained.",
                len(recovered),
            )
            return recovered
        partial_map = getattr(self, "_phase3_partial_updates", {})
        extras: list[Update] = []
        if isinstance(partial_map, dict):
            for updates in partial_map.values():
                if isinstance(updates, list):
                    extras.extend(item for item in updates if isinstance(item, Update))
        return _source_authority._configured_only(self, [*result, *extras])
    finally:
        self._phase3_allow_older_checkpoint = previous_flag
        self._phase3_partial_updates = previous_partial if isinstance(previous_partial, dict) else {}
        self._phase3_syndication_fallback_count = previous_fallback_count


def _install_collector_state_binding() -> None:
    if Application.__dict__.get("_phase3_state_binding_installed", False):
        return
    original_init = Application.__init__

    def init(self, settings):
        original_init(self, settings)
        self.collector._phase3_state = self.state

    Application.__init__ = init
    Application._phase3_state_binding_installed = True


def install() -> None:
    _install_state_checkpoints()
    _install_collector_state_binding()
    CompleteWindowXCollector._collect_source_timeline = _resumable_source_timeline
    XCollector._collect_source_timeline = _resumable_source_timeline
    XCollector.collect_window = _resumable_collect_window


install()
