"""Keep automatic Daily collection faithful to the configured X sources.

The user-curated source list is authoritative for automatic Daily/recent-window
collection: every original post/reply/media item returned from those accounts must
survive relevance filtering, even when the caption does not spell out Jeonghan.
Keyword search remains useful as a recovery/discovery path, but automatic windows
must not emit posts from accounts outside the configured source list.

Explicit archive search keeps its historical broader discovery behavior.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from . import x_client as _x_client
from .models import Update

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
    """Return only configured-source posts for automatic bounded windows.

    We still allow the original collector to run its keyword queries because they
    can recover a configured-source post that a timeline path happened to miss.
    The final boundary, however, is the user's configured source list.
    """
    updates = await _original_collect_window(
        self,
        start,
        end,
        include_sources=include_sources,
        include_keywords=include_keywords,
        max_per_query=max_per_query,
    )
    if not include_sources:
        return updates
    configured = _configured_handles(self)
    return [item for item in updates if item.author.casefold() in configured]


# This repository already installs small deterministic runtime hardening layers from
# app.__init__. Keep this patch equally narrow and explicit instead of duplicating the
# X client implementation.
_x_client.XCollector._filter_relevant = _source_authoritative_filter
_x_client.XCollector.collect_window = _sources_only_collect_window
