"""Enforce configured-source authority for every non-Fanfic X retrieval path.

The user-curated X source list is authoritative for Daily monitoring, recent/2h,
24h source replay, manual/archive search, event recovery, and every caller that uses
``XCollector``. Fanfic/AO3 remains independent: its optional X recommendation layer
uses ``XCollector._get_api()`` directly and does not call the retrieval methods
patched here.

Configured-source timelines are completeness-aware. A bounded window is complete
only after the lower boundary is crossed or the source timeline naturally exhausts.
Search recovery may return useful partial data from the *same configured author*,
but a failed timeline remains recorded in ``last_errors`` so production never moves
the success cursor past missing content.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from . import x_client as _x_client
from .models import Update
from .x_completeness import CompleteWindowXCollector, XCompletenessError

logger = logging.getLogger(__name__)
_FROM_OPERATOR_RE = re.compile(r"(?<!\S)from:[A-Za-z0-9_]{1,15}(?=\s|$)", re.I)


def _configured_handles(collector: _x_client.XCollector) -> set[str]:
    handles: set[str] = set()
    for source in collector.sources:
        if not source.get("enabled", True):
            continue
        handle = _x_client.normalize_handle(str(source.get("handle", "")))
        if handle:
            handles.add(handle.casefold())
    return handles


def _source_config(collector: _x_client.XCollector, handle: str) -> dict | None:
    normalized = _x_client.normalize_handle(handle).casefold()
    if not normalized:
        return None
    for source in collector.sources:
        if not source.get("enabled", True):
            continue
        candidate = _x_client.normalize_handle(str(source.get("handle", "")))
        if candidate and candidate.casefold() == normalized:
            return source
    return None


def _is_configured_source(self: _x_client.XCollector, handle: str) -> bool:
    return _source_config(self, handle) is not None


def _configured_only(
    self: _x_client.XCollector,
    updates: Iterable[Update],
) -> list[Update]:
    """Keep only configured authors and deterministically dedupe/order by post ID."""
    configured = _configured_handles(self)
    chosen: dict[str, Update] = {}
    for item in updates:
        if item.author.casefold() not in configured:
            logger.info("Dropped non-configured X item %s from @%s", item.id, item.author)
            continue
        current = chosen.get(item.id)
        if current is None:
            chosen[item.id] = item
            continue
        current_score = (
            len(current.media) + len(current.quoted_media),
            len(current.text) + len(current.quoted_text),
            bool(current.conversation_id),
            bool(current.reply_to_id),
            -current.source_priority,
        )
        item_score = (
            len(item.media) + len(item.quoted_media),
            len(item.text) + len(item.quoted_text),
            bool(item.conversation_id),
            bool(item.reply_to_id),
            -item.source_priority,
        )
        if item_score > current_score:
            chosen[item.id] = item
    ordered = sorted(chosen.values(), key=lambda item: (item.created_at, item.id))
    gate = getattr(self, "source_mode_gate", None)
    if gate is None:
        raise RuntimeError("source-mode gate is not installed")
    return gate.filter_posts(ordered)


def _source_authoritative_filter(
    self: _x_client.XCollector,
    updates: list[Update],
) -> list[Update]:
    """Configured authors are the only valid non-Fanfic X output."""
    return _configured_only(self, updates)


def _strip_from_operators(query: str) -> str:
    return re.sub(r"\s+", " ", _FROM_OPERATOR_RE.sub(" ", str(query or ""))).strip()


def _scoped_queries(handle: str, queries: Iterable[str], suffix: str = "") -> list[str]:
    scoped: list[str] = []
    for raw in queries:
        query = _strip_from_operators(raw)
        if not query:
            continue
        scoped.append(f"from:{handle} {query} {suffix}".strip())
    return _x_client._unique(scoped)


async def _sources_only_collect_window(
    self: _x_client.XCollector,
    start,
    end,
    *,
    include_sources: bool = True,
    include_keywords: bool = True,
    max_per_query: int = 60,
) -> list[Update]:
    """Collect a bounded window exclusively from configured source accounts.

    Healthy source timelines are authoritative. When a timeline fails, an
    author-scoped search can recover useful posts from that same source, but the
    timeline failure remains in ``last_errors`` and therefore keeps the production
    cursor pinned for retry.

    ``include_sources=False`` is also source-only: it performs configured-author
    search recovery instead of falling back to the old global keyword search.
    """
    self.last_errors = []
    results: list[Update] = []
    errors: list[str] = []
    enabled_sources = [source for source in self.sources if source.get("enabled", True)]
    timeline_limit = max(200, min(1000, max_per_query * 3))
    date_suffix = _x_client._date_suffix(start, end)

    for source_index, source in enumerate(enabled_sources):
        handle = _x_client.normalize_handle(str(source.get("handle", "")))
        if not handle:
            continue
        timeline_failed = not include_sources
        if include_sources:
            try:
                results.extend(
                    await self._collect_source_timeline(
                        handle,
                        start,
                        end,
                        limit=timeline_limit,
                        include_replies=bool(source.get("include_replies", True)),
                    )
                )
            except _x_client.XCollectionError as exc:
                timeline_failed = True
                errors.append(f"@{handle}: {_x_client._safe_error(exc)}")

        if timeline_failed and include_keywords:
            # Recovery deliberately asks for *all* posts by the configured author in
            # the window. Relevance keywords must never make a real source media post
            # disappear merely because its caption omits Jeonghan's name.
            query = f"from:{handle} -filter:retweets {date_suffix}".strip()
            try:
                recovered = await self._run_queries(
                    [query],
                    start,
                    end,
                    max_per_query=timeline_limit,
                )
                handle_key = handle.casefold()
                results.extend(item for item in recovered if item.author.casefold() == handle_key)
            except _x_client.XCollectionError as recovery_exc:
                errors.append(f"@{handle} recovery: {_x_client._safe_error(recovery_exc)}")
        if source_index + 1 < len(enabled_sources):
            await asyncio.sleep(_x_client.X_SOURCE_PACE_SECONDS)

    self.last_errors = _x_client._unique(self.last_errors + errors)
    return _configured_only(self, results)


async def _configured_collect_source(
    self: _x_client.XCollector,
    handle: str,
    start,
    end,
) -> list[Update]:
    """Return a proven-complete configured-source window using its reply policy."""
    handle = _x_client.normalize_handle(handle)
    if not handle:
        raise _x_client.XCollectionError("Source handle is invalid.")
    source = _source_config(self, handle)
    if source is None:
        raise _x_client.XCollectionError(
            f"@{handle} is not in the configured source list; only configured sources can be retrieved."
        )
    include_replies = bool(source.get("include_replies", True))
    try:
        results = await self._collect_source_timeline(
            handle,
            start,
            end,
            limit=1000,
            include_replies=include_replies,
        )
        return _configured_only(self, results)
    except XCompletenessError:
        raise
    except _x_client.XCollectionError as exc:
        raise XCompletenessError(
            f"Could not prove a complete source timeline for @{handle}; "
            f"search-only fallback was not used. {_x_client._safe_error(exc)}"
        ) from exc


async def _configured_search_archive(
    self: _x_client.XCollector,
    queries: Iterable[str],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    max_per_query: int = 100,
) -> list[Update]:
    """Search X only through author-scoped queries for configured sources."""
    query_list = [str(query).strip() for query in queries if str(query).strip()]
    if not query_list:
        return []
    lower = start or datetime(2006, 1, 1, tzinfo=timezone.utc)
    upper = end or datetime.now(timezone.utc) + timedelta(days=1)
    suffix = _x_client._date_suffix(start, end) if start and end else ""
    results: list[Update] = []
    errors: list[str] = []

    for source_index, source in enumerate(
        item for item in self.sources if item.get("enabled", True)
    ):
        handle = _x_client.normalize_handle(str(source.get("handle", "")))
        if not handle:
            continue
        scoped = _scoped_queries(handle, query_list, suffix)
        if not scoped:
            continue
        try:
            results.extend(
                await self._run_queries(
                    scoped,
                    lower,
                    upper,
                    max_per_query=max_per_query,
                    product="Latest",
                )
            )
            # Keep the old Top signal, but it is equally author-scoped.
            results.extend(
                await self._run_queries(
                    scoped[:4],
                    lower,
                    upper,
                    max_per_query=min(60, max_per_query),
                    product="Top",
                )
            )
        except _x_client.XCollectionError as exc:
            errors.append(f"@{handle} search: {_x_client._safe_error(exc)}")
        if source_index + 1 < len([s for s in self.sources if s.get("enabled", True)]):
            await asyncio.sleep(_x_client.X_SOURCE_PACE_SECONDS)

    if errors:
        self.last_errors = _x_client._unique(self.last_errors + errors)
    filtered = _configured_only(self, results)
    if not filtered and errors:
        raise _x_client.XCollectionError(
            "Configured-source X search returned no usable result. " + " | ".join(errors[:3])
        )
    return filtered


async def _configured_collect_event(
    self: _x_client.XCollector,
    selected: Update,
) -> list[Update]:
    """Recover a selected event without ever querying or returning outside authors."""
    if not _is_configured_source(self, selected.author):
        raise _x_client.XCollectionError(
            f"@{selected.author} is not a configured source; event recovery was blocked."
        )
    api = await self._get_api()
    start = selected.created_at - timedelta(hours=8)
    end = selected.created_at + timedelta(hours=18)
    results: list[Update] = []
    errors: list[str] = []
    thread_id = selected.conversation_id or selected.id
    configured = _configured_handles(self)

    if thread_id.isdigit():
        try:
            async for tweet in api.tweet_thread(int(thread_id), limit=300):
                update = self._convert_tweet(tweet, raw_query=f"thread:{thread_id}")
                if (
                    update
                    and update.author.casefold() in configured
                    and start <= update.created_at < end
                ):
                    results.append(update)
        except Exception as exc:
            errors.append("thread: " + _x_client._safe_error(exc))

    tokens = _x_client._event_terms(selected.text)
    queries = [f"from:{selected.author} {_x_client._date_suffix(start, end)}"]
    if tokens:
        joined = " ".join(tokens[:4])
        queries.extend(
            [
                f"from:{selected.author} {joined} JEONGHAN {_x_client._date_suffix(start, end)}",
                f"from:{selected.author} {joined} 윤정한 {_x_client._date_suffix(start, end)}",
                f"from:{selected.author} {joined} ジョンハン {_x_client._date_suffix(start, end)}",
            ]
        )
    try:
        results.extend(await self._run_queries(queries, start, end, max_per_query=220))
    except _x_client.XCollectionError as exc:
        errors.append(_x_client._safe_error(exc))
    if selected.id not in {item.id for item in results}:
        results.append(selected)

    kept: list[Update] = []
    selected_author = selected.author.casefold()
    for item in _configured_only(self, results):
        same_author = item.author.casefold() == selected_author
        same_conversation = item.conversation_id == selected.conversation_id
        close = abs((item.created_at - selected.created_at).total_seconds()) <= 8 * 3600
        live_related = _x_client._looks_live(item.text) and _x_client._looks_live(selected.text)
        if item.id == selected.id or (same_author and same_conversation) or (
            same_author and close and live_related
        ):
            kept.append(item)
    kept = _configured_only(self, kept)
    if not kept and errors:
        raise _x_client.XCollectionError(
            "Could not collect the selected configured-source event. " + " | ".join(errors[:3])
        )
    return kept


_x_client.XCollector.is_configured_source = _is_configured_source
_x_client.XCollector.filter_configured_updates = _configured_only
_x_client.XCollector._filter_relevant = _source_authoritative_filter
_x_client.XCollector._collect_source_timeline = CompleteWindowXCollector._collect_source_timeline
_x_client.XCollector.collect_source = _configured_collect_source
_x_client.XCollector.collect_window = _sources_only_collect_window
_x_client.XCollector.search_archive = _configured_search_archive
_x_client.XCollector.collect_event = _configured_collect_event
