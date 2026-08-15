"""Install shadow Event grouping on the real private-review delivery override."""
from __future__ import annotations

from . import event_fusion
from .models import Update
from .observability import observe
from .private_runtime import PrivateReviewApplication


def _configured(application: PrivateReviewApplication) -> set[str]:
    return {
        str(item.get("handle", "")).lstrip("@").strip().casefold()
        for item in application.settings.sources
        if item.get("enabled", True) and str(item.get("handle", "")).strip()
    }


def ensure_translation_fusion_shadow() -> None:
    """Lazy-load Translation Fusion only for the non-Fanfic private-review path."""
    if getattr(event_fusion.shadow_group_updates, "_translation_fusion_shadow_installed", False):
        return

    from . import translation_fusion
    from . import translation_fusion_runtime
    from . import translation_fusion_state_compat

    translation_fusion_state_compat.install(event_fusion, translation_fusion)
    translation_fusion_runtime.install(event_fusion)


def _install() -> None:
    current = PrivateReviewApplication.deliver_updates
    if getattr(current, "_event_fusion_private_shadow_installed", False):
        return

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        try:
            ensure_translation_fusion_shadow()
            event_fusion.shadow_group_updates(self.state, updates, _configured(self))
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
        return await current(self, updates, force=force)

    deliver_updates._event_fusion_private_shadow_installed = True
    PrivateReviewApplication.deliver_updates = deliver_updates


_install()
