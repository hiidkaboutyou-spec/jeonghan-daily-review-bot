"""Install Translation Fusion after Timeline in the existing Event shadow chain."""
from __future__ import annotations

from typing import Any

from .observability import observe
from .translation_fusion import TRANSLATION_FUSION_MODE, shadow_fuse_translations


def install(event_fusion: Any) -> None:
    current = event_fusion.shadow_group_updates
    if getattr(current, "_translation_fusion_shadow_installed", False):
        return

    def shadow_group_updates(state, updates, configured_handles):
        items = list(updates)
        # Event + Timeline remain authoritative upstream shadow analysis.
        event_results = current(state, items, configured_handles)
        try:
            shadow_fuse_translations(state, items, configured_handles)
        except Exception as exc:
            observe(
                "shadow_translation_fusion",
                level="warning",
                component="translation_fusion",
                stage="translation_fidelity_shadow",
                status="failed",
                error_class=type(exc).__name__,
                source="configured_segment_evidence",
            )
        # Never replace Event results or affect delivery.
        return event_results

    # Preserve the existing Timeline wrapper contract when adding the outer
    # Translation Fusion shadow layer. This marker is observability/ordering metadata,
    # not delivery authority, and must survive wrapper composition.
    if getattr(current, "_event_timeline_shadow_installed", False):
        shadow_group_updates._event_timeline_shadow_installed = True
    shadow_group_updates._translation_fusion_shadow_installed = True
    event_fusion.shadow_group_updates = shadow_group_updates
