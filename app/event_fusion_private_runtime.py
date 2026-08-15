"""Install shadow Event grouping on the real private-review delivery override."""
from __future__ import annotations

from .event_fusion import EVENT_MODE, shadow_group_updates
from .models import Update
from .observability import observe
from .private_runtime import PrivateReviewApplication


def _configured(application: PrivateReviewApplication) -> set[str]:
    return {
        str(item.get("handle", "")).lstrip("@").strip().casefold()
        for item in application.settings.sources
        if item.get("enabled", True) and str(item.get("handle", "")).strip()
    }


def _install() -> None:
    current = PrivateReviewApplication.deliver_updates
    if getattr(current, "_event_fusion_private_shadow_installed", False):
        return

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        try:
            shadow_group_updates(self.state, updates, _configured(self))
        except Exception as exc:
            observe(
                "shadow_event_grouping",
                level="warning",
                component="event_fusion",
                stage="shadow_grouping",
                status="failed",
                error_class=type(exc).__name__,
                grouping_mode=EVENT_MODE,
            )
        return await current(self, updates, force=force)

    deliver_updates._event_fusion_private_shadow_installed = True
    PrivateReviewApplication.deliver_updates = deliver_updates


_install()
