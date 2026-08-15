"""Install shadow Event/Timeline/Translation/Style analysis on private-review delivery."""
from __future__ import annotations

from . import event_fusion
from .models import Update
from .observability import observe
from .private_runtime import PrivateReviewApplication


def _configured(application: PrivateReviewApplication) -> set[str] | None:
    """Return configured handles when runtime settings expose the real source list.

    Some isolated delivery tests intentionally construct a minimal application or
    settings object without sources. Shadow analysis is optional, so those cases
    must preserve the pre-shadow delivery path instead of becoming a new delivery
    requirement.
    """
    settings = getattr(application, "settings", None)
    sources = getattr(settings, "sources", None)
    if not isinstance(sources, (list, tuple)):
        return None
    return {
        str(item.get("handle", "")).lstrip("@").strip().casefold()
        for item in sources
        if isinstance(item, dict)
        and item.get("enabled", True)
        and str(item.get("handle", "")).strip()
    }


def ensure_translation_fusion_shadow() -> None:
    """Lazy-load non-Fanfic Translation/Style/Calibration shadow state."""
    if not getattr(event_fusion.shadow_group_updates, "_translation_fusion_shadow_installed", False):
        from . import translation_fusion
        from . import translation_fusion_runtime
        from . import translation_fusion_state_compat

        translation_fusion_state_compat.install(event_fusion, translation_fusion)
        translation_fusion_runtime.install(event_fusion)

    # Style Rewrite remains lazy and non-Fanfic. Install only its durable metadata
    # sanitizer here; candidate evaluation runs after the Translation wrapper below.
    from . import channel_style_rewrite
    from . import channel_style_rewrite_state_compat

    channel_style_rewrite_state_compat.install(event_fusion, channel_style_rewrite)

    # User-Voice Calibration is metadata-only and AUTO_LEARN=false.  It does not
    # consume edits, alter ranking, or own any delivery path automatically.  This
    # compatibility hook only permits bounded reversible calibration metadata to
    # survive the existing Event durable-state sanitizer when explicit evidence is
    # supplied by a future isolated review-evidence path.
    from . import user_voice_calibration
    from . import user_voice_calibration_state_compat

    user_voice_calibration_state_compat.install(event_fusion, user_voice_calibration)


def _install() -> None:
    current = PrivateReviewApplication.deliver_updates
    if getattr(current, "_event_fusion_private_shadow_installed", False):
        return

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        configured = _configured(self)
        if configured is None:
            # Shadow-only analysis must never make source configuration a new
            # precondition for otherwise-valid private delivery/test fixtures.
            return await current(self, updates, force=force)

        shadow_chain_ok = False
        try:
            ensure_translation_fusion_shadow()
            event_fusion.shadow_group_updates(self.state, updates, configured)
            shadow_chain_ok = True
        except Exception as exc:
            observe(
                "shadow_event_grouping",
                level="warning",
                component="event_fusion",
                stage="shadow_grouping",
                status="failed",
                error_class=type(exc).__name__,
                grouping_mode=event_fusion.EVENT_MODE,
            )

        if shadow_chain_ok:
            try:
                from .channel_style_rewrite import shadow_style_rewrite

                shadow_style_rewrite(self.state, self.memory, updates, configured)
            except Exception as exc:
                observe(
                    "shadow_channel_style_rewrite",
                    level="warning",
                    component="channel_style_rewrite",
                    stage="channel_style_shadow",
                    status="failed",
                    error_class=type(exc).__name__,
                    source="faithful_factual_persian",
                )

        # Existing private-review delivery remains authoritative regardless of every
        # shadow result or failure. Voice Calibration never changes this return path.
        return await current(self, updates, force=force)

    deliver_updates._event_fusion_private_shadow_installed = True
    PrivateReviewApplication.deliver_updates = deliver_updates


_install()
