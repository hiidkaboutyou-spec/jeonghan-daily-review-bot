"""Install shadow Timeline analysis immediately after shadow Event grouping."""
from __future__ import annotations

from typing import Any

from .event_timeline import TIMELINE_MODE, shadow_segment_events
from .observability import observe


def install(event_fusion: Any) -> None:
    current = event_fusion.shadow_group_updates
    if getattr(current, "_event_timeline_shadow_installed", False):
        return

    def shadow_group_updates(state, updates, configured_handles):
        items = list(updates)
        event_results = current(state, items, configured_handles)
        try:
            shadow_segment_events(state, items, configured_handles)
        except Exception as exc:
            observe(
                "shadow_event_timeline",
                level="warning",
                component="event_timeline",
                stage="shadow_timeline",
                status="failed",
                error_class=type(exc).__name__,
                timeline_mode=TIMELINE_MODE,
            )
        return event_results

    shadow_group_updates._event_timeline_shadow_installed = True
    event_fusion.shadow_group_updates = shadow_group_updates
