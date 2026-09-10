from __future__ import annotations

"""Runtime recovery for provider-wide authenticated X outages.

The normal collector remains authoritative while authenticated X is healthy. When the
production preflight marks X as degraded, scheduled/manual window collection bypasses
repeated authenticated retries and reads a bounded rotating batch of configured public
profile syndication feeds instead. The fallback is intentionally marked partial so the
normal success cursor is retained and the missed window is backfilled after X recovers.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

from .models import Update, ensure_utc
from .x_client import XCollectionError, XCollector, _safe_error, normalize_handle
from .x_syndication import SyndicationError, collect_syndication_timeline

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 8
MAX_BATCH_SIZE = 16
DEFAULT_CONCURRENCY = 4

_ORIGINAL_COLLECT_WINDOW = XCollector.collect_window
_ORIGINAL_COLLECT_SOURCE = XCollector.collect_source


def _provider_state() -> str:
    return os.environ.get("X_PROVIDER_PREFLIGHT", "").strip().lower()


def _degraded() -> bool:
    return _provider_state() == "degraded"


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _enabled_sources(self: XCollector) -> list[dict[str, Any]]:
    items = [item for item in self.sources if item.get("enabled", True)]
    # Preserve configured order inside the same priority while making the most
    # important sources first in every rotation cycle.
    return sorted(
        items,
        key=lambda item: int(item.get("priority", 100) or 100),
    )


def _rotation_start(self: XCollector, source_count: int, batch_size: int) -> int:
    if source_count <= batch_size:
        return 0
    state = getattr(self, "_phase3_state", None)
    data = getattr(state, "data", {}) if state is not None else {}
    try:
        streak = max(0, int(data.get("x_scan_failure_streak", 0) or 0))
    except (AttributeError, TypeError, ValueError):
        streak = 0
    return (streak * batch_size) % source_count


def _select_rotating_batch(self: XCollector) -> tuple[list[dict[str, Any]], int]:
    sources = _enabled_sources(self)
    if not sources:
        return [], 0
    batch_size = min(
        len(sources),
        _positive_int_env("X_SYNDICATION_FALLBACK_BATCH_SIZE", DEFAULT_BATCH_SIZE, MAX_BATCH_SIZE),
    )
    start = _rotation_start(self, len(sources), batch_size)
    selected = [sources[(start + index) % len(sources)] for index in range(batch_size)]
    return selected, len(sources)


def _dedupe(updates: list[Update]) -> list[Update]:
    chosen: dict[str, Update] = {}
    for item in updates:
        if item.id:
            chosen[item.id] = item
    return sorted(chosen.values(), key=lambda item: (item.created_at, item.id))


async def _collect_public_source(
    self: XCollector,
    source: dict[str, Any],
    start: datetime,
    end: datetime,
    semaphore: asyncio.Semaphore,
) -> tuple[str, list[Update], str | None]:
    handle = normalize_handle(str(source.get("handle", "")))
    if not handle:
        return "", [], "invalid configured source handle"
    include_replies = bool(source.get("include_replies", True))
    try:
        async with semaphore:
            result = await asyncio.to_thread(
                collect_syndication_timeline,
                handle,
                ensure_utc(start),
                ensure_utc(end),
                include_replies=include_replies,
            )
    except (SyndicationError, OSError, ValueError) as exc:
        return handle, [], _safe_error(exc)
    except Exception as exc:  # defensive boundary: never let one public source abort the batch
        return handle, [], f"{type(exc).__name__}: {_safe_error(exc)}"
    return handle, list(result.updates), None


async def collect_degraded_window(
    self: XCollector,
    start: datetime,
    end: datetime,
    *,
    include_sources: bool = True,
    include_keywords: bool = True,
    max_per_query: int = 60,
) -> list[Update]:
    """Collect a source-authorized partial window without touching authenticated X."""
    del max_per_query  # Public profile fallback has no search-query pagination control.
    self.last_errors = []
    if not include_sources:
        self.last_errors = ["authenticated_x_degraded: keyword-only collection unavailable"]
        return []

    selected, total_sources = _select_rotating_batch(self)
    if not selected:
        self.last_errors = ["authenticated_x_degraded: no configured public sources"]
        return []

    concurrency = _positive_int_env(
        "X_SYNDICATION_FALLBACK_CONCURRENCY",
        DEFAULT_CONCURRENCY,
        DEFAULT_CONCURRENCY,
    )
    semaphore = asyncio.Semaphore(concurrency)
    rows = await asyncio.gather(
        *(
            _collect_public_source(self, source, start, end, semaphore)
            for source in selected
        )
    )

    updates: list[Update] = []
    errors: list[str] = ["authenticated_x_degraded: public_syndication_fallback"]
    succeeded = 0
    for handle, recovered, error in rows:
        if error:
            errors.append(f"@{handle}: syndication_fallback_failed ({error})" if handle else error)
            continue
        succeeded += 1
        updates.extend(recovered)

    if len(selected) < total_sources:
        errors.append(f"syndication_coverage_partial: {len(selected)}/{total_sources} sources this pass")
    if include_keywords:
        errors.append("keyword_search_unavailable: authenticated_x_degraded")

    self.last_errors = errors
    filtered = self._filter_relevant(_dedupe(updates))
    logger.warning(
        "Authenticated X is degraded; public syndication recovered %s update(s) from %s/%s selected sources. Success cursor remains retained.",
        len(filtered),
        succeeded,
        len(selected),
    )
    return filtered


async def _collect_window_with_provider_recovery(
    self: XCollector,
    start: datetime,
    end: datetime,
    *,
    include_sources: bool = True,
    include_keywords: bool = True,
    max_per_query: int = 60,
) -> list[Update]:
    if not _degraded():
        return await _ORIGINAL_COLLECT_WINDOW(
            self,
            start,
            end,
            include_sources=include_sources,
            include_keywords=include_keywords,
            max_per_query=max_per_query,
        )
    return await collect_degraded_window(
        self,
        start,
        end,
        include_sources=include_sources,
        include_keywords=include_keywords,
        max_per_query=max_per_query,
    )


async def _collect_source_with_provider_recovery(
    self: XCollector,
    handle: str,
    start: datetime,
    end: datetime,
) -> list[Update]:
    normalized = normalize_handle(handle)
    if not normalized:
        raise XCollectionError("Source handle is invalid.")

    if not _degraded():
        try:
            return await _ORIGINAL_COLLECT_SOURCE(self, normalized, start, end)
        except XCollectionError as original_error:
            logger.warning(
                "Authenticated X source read failed for @%s; trying one public syndication recovery: %s",
                normalized,
                _safe_error(original_error),
            )

    try:
        result = await asyncio.to_thread(
            collect_syndication_timeline,
            normalized,
            ensure_utc(start),
            ensure_utc(end),
            include_replies=True,
        )
    except (SyndicationError, OSError, ValueError) as exc:
        raise XCollectionError(
            f"Could not read @{normalized} from authenticated X or public fallback: {_safe_error(exc)}"
        ) from exc

    self.last_errors = ["authenticated_x_degraded: public_syndication_fallback"] if _degraded() else []
    return _dedupe(list(result.updates))[:1000]


def install() -> None:
    if XCollector.__dict__.get("_provider_recovery_installed", False):
        return
    XCollector.collect_window = _collect_window_with_provider_recovery
    XCollector.collect_source = _collect_source_with_provider_recovery
    XCollector._provider_recovery_installed = True


install()
