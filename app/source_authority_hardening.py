"""Enforce configured-source authority for every non-Fanfic X retrieval path.

The user-curated source list is authoritative for all normal Jeonghan update
collection. Every original post/reply/media item returned from an enabled configured
account must survive relevance filtering even when the caption does not spell out
Jeonghan. Keyword/search queries are discovery or recovery only and may never emit
content authored by an unconfigured account.

Fanfic/AO3 is intentionally outside this policy. Its X recommendation collector uses
the low-level X API directly and does not call the normal update retrieval methods
patched here.

Configured-source timelines use the repository's completeness-aware collector: a
bounded window is complete only after the lower time boundary is crossed or the
source timeline naturally exhausts. Search recovery can provide useful partial data,
but a failed timeline remains marked incomplete so production does not advance its
success cursor as if the source window were complete.
"""

from __future__ import annotations

import asyncio
import logging

from . import x_client as _x_client
from .models import Update
from .x_completeness import CompleteWindowXCollector, XCompletenessError

logger = logging.getLogger(__name__)

_original_collect_window = _x_client.XCollector.collect_window
_original_collect_event = _x_client.XCollector.collect_event


def configured_handles(collector: _x_client.XCollector) -> set[str]:
    """Return normalized enabled source handles for this collector."""
    handles: set[str] = set()
    for source in collector.sources:
        if not source.get("enabled", True):
            continue
        handle = _x_client.normalize_handle(str(source.get("handle", "")))
        if handle:
            handles.add(handle.casefold())
    return handles


def is_configured_author(collector: _x_client.XCollector, author: str) -> bool:
    return _x_client.normalize_handle(author).casefold() in configured_handles(collector)


def filter_configured_updates(
    collector: _x_client.XCollector,
    updates: list[Update],
) -> list[Update]:
    """Keep only enabled configured-source authors and preserve deterministic order.

    This helper is also used by the private-review runtime as a final defense against
    stale pre-policy queue/session/archive rows.
    """
    allowed = configured_handles(collector)
    kept = [item for item in _x_client._dedupe(updates) if item.author.casefold() in allowed]
    kept.sort(key=lambda item: (item.created_at, item.id))
    return kept


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


def _source_authoritative_filter(
    self: _x_client.XCollector,
    updates: list[Update],
) -> list[Update]:
    """Normal update retrieval must never emit an unconfigured author."""
    allowed = configured_handles(self)
    kept: list[Update] = []
    for item in updates:
        if item.author.casefold() in allowed:
            kept.append(item)
        else:
            logger.info(
                "Filtered non-configured X author for normal update flow: post=%s author=@%s",
                item.id,
                item.author,
            )
    return _x_client._dedupe(kept)


async def _sources_only_collect_window(
    self: _x_client.XCollector,
    start,
    end,
    *,
    include_sources: bool = True,
    include_keywords: bool = True,
    max_per_query: int = 60,
) -> list[Update]:
    """Return configured-source posts for automatic bounded windows.

    A healthy configured timeline is authoritative and needs no keyword query. If a
    timeline path fails, a source-scoped search may recover useful posts from that
    same account, but the timeline error remains in ``last_errors``. Production can
    therefore queue the partial data while retaining the previous success cursor for
    a later complete retry.
    """
    if not include_sources:
        # This compatibility mode is still source-authoritative. Some tests/tools can
        # request search-only collection, but external authors must not enter the
        # normal update flow.
        updates = await _original_collect_window(
            self,
            start,
            end,
            include_sources=False,
            include_keywords=include_keywords,
            max_per_query=max_per_query,
        )
        return filter_configured_updates(self, updates)

    self.last_errors = []
    results: list[Update] = []
    errors: list[str] = []
    enabled_sources = [source for source in self.sources if source.get("enabled", True)]
    timeline_limit = max(200, min(1000, max_per_query * 3))

    for source_index, source in enumerate(enabled_sources):
        handle = _x_client.normalize_handle(str(source.get("handle", "")))
        if not handle:
            continue
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
            errors.append(f"@{handle}: {_x_client._safe_error(exc)}")
            if include_keywords:
                query = f"from:{handle} -filter:retweets {_x_client._date_suffix(start, end)}"
                try:
                    recovered = await self._run_queries(
                        [query],
                        start,
                        end,
                        max_per_query=timeline_limit,
                    )
                    handle_key = handle.casefold()
                    results.extend(
                        item for item in recovered if item.author.casefold() == handle_key
                    )
                except _x_client.XCollectionError as recovery_exc:
                    errors.append(
                        f"@{handle} recovery: {_x_client._safe_error(recovery_exc)}"
                    )
        if source_index + 1 < len(enabled_sources):
            await asyncio.sleep(_x_client.X_SOURCE_PACE_SECONDS)

    self.last_errors = _x_client._unique(self.last_errors + errors)
    return filter_configured_updates(self, results)


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
            f"@{handle} is not in the configured source list; normal update retrieval is source-only."
        )
    include_replies = bool(source.get("include_replies", True))
    try:
        updates = await self._collect_source_timeline(
            handle,
            start,
            end,
            limit=1000,
            include_replies=include_replies,
        )
        return filter_configured_updates(self, updates)
    except XCompletenessError:
        raise
    except _x_client.XCollectionError as exc:
        raise XCompletenessError(
            f"Could not prove a complete source timeline for @{handle}; "
            f"search-only fallback was not used. {_x_client._safe_error(exc)}"
        ) from exc


async def _configured_collect_event(
    self: _x_client.XCollector,
    selected: Update,
) -> list[Update]:
    """Reconstruct an event only when its selected author is configured."""
    if not is_configured_author(self, selected.author):
        raise _x_client.XCollectionError(
            f"@{selected.author} is not in the configured source list; event replay was blocked."
        )
    updates = await _original_collect_event(self, selected)
    return filter_configured_updates(self, updates)


_x_client.XCollector._filter_relevant = _source_authoritative_filter
_x_client.XCollector._collect_source_timeline = CompleteWindowXCollector._collect_source_timeline
_x_client.XCollector.collect_source = _configured_collect_source
_x_client.XCollector.collect_window = _sources_only_collect_window
_x_client.XCollector.collect_event = _configured_collect_event
