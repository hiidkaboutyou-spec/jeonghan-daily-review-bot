"""Keep automatic Daily collection faithful to the configured X sources.

The user-curated source list is authoritative for automatic Daily/recent-window
collection: every original post/reply/media item returned from those accounts must
survive relevance filtering, even when the caption does not spell out Jeonghan.
Keyword search is recovery-only for automatic windows and is always scoped back to
the configured source whose timeline failed. Manual archive search keeps its wider
historical discovery behavior.

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


def _source_authoritative_filter(
    self: _x_client.XCollector,
    updates: list[Update],
) -> list[Update]:
    """Never silently discard a post authored by a configured source.

    Non-source results keep the existing Jeonghan/noise rules so manual archive
    discovery does not become an unrestricted X firehose.
    """
    configured = _configured_handles(self)
    kept: list[Update] = []
    for item in updates:
        author = item.author.casefold()
        if author in configured:
            kept.append(item)
            continue
        if _x_client.is_relevant_jeonghan_update(item, trusted_source=False):
            kept.append(item)
        else:
            logger.info(
                "Filtered non-source/non-Jeonghan post %s from @%s",
                item.id,
                item.author,
            )
    return kept


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
        return await _original_collect_window(
            self,
            start,
            end,
            include_sources=False,
            include_keywords=include_keywords,
            max_per_query=max_per_query,
        )

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
    configured = _configured_handles(self)
    results = [
        item
        for item in _x_client._dedupe(results)
        if item.author.casefold() in configured
    ]
    results.sort(key=lambda item: (item.created_at, item.id))
    return results


async def _configured_collect_source(
    self: _x_client.XCollector,
    handle: str,
    start,
    end,
) -> list[Update]:
    """Return a proven-complete 24h source window using that source's reply policy."""
    handle = _x_client.normalize_handle(handle)
    if not handle:
        raise _x_client.XCollectionError("Source handle is invalid.")

    source = _source_config(self, handle)
    include_replies = bool(source.get("include_replies", True)) if source else True
    try:
        return await self._collect_source_timeline(
            handle,
            start,
            end,
            limit=1000,
            include_replies=include_replies,
        )
    except XCompletenessError:
        raise
    except _x_client.XCollectionError as exc:
        raise XCompletenessError(
            f"Could not prove a complete source timeline for @{handle}; "
            f"search-only fallback was not used. {_x_client._safe_error(exc)}"
        ) from exc


_x_client.XCollector._filter_relevant = _source_authoritative_filter
_x_client.XCollector._collect_source_timeline = CompleteWindowXCollector._collect_source_timeline
_x_client.XCollector.collect_source = _configured_collect_source
_x_client.XCollector.collect_window = _sources_only_collect_window
