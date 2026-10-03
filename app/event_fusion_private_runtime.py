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

    from . import channel_style_rewrite
    from . import channel_style_rewrite_state_compat

    channel_style_rewrite_state_compat.install(event_fusion, channel_style_rewrite)

    from . import user_voice_calibration
    from . import user_voice_calibration_state_compat

    user_voice_calibration_state_compat.install(event_fusion, user_voice_calibration)

    from . import forward_ready_package
    from . import forward_ready_state_compat

    forward_ready_state_compat.install(event_fusion, forward_ready_package)


def _install() -> None:
    current = PrivateReviewApplication.deliver_updates
    if getattr(current, "_event_fusion_private_shadow_installed", False):
        return

    async def deliver_updates(self, updates: list[Update], *, force: bool) -> None:
        configured = _configured(self)
        if configured is None:
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

        # HARD DEFAULT: the existing Update-oriented private-review delivery still
        # completes first and remains authoritative. The authority foundation below
        # only evaluates privacy-safe future-switch metadata after this send. It
        # cannot cause fused network traffic in this PR.
        result = await current(self, updates, force=force)
        forward_ready_packages = None
        if shadow_chain_ok:
            try:
                from .forward_ready_package import plan_forward_ready_packages

                forward_ready_packages = plan_forward_ready_packages(
                    self.state,
                    updates,
                    final_edit_store=getattr(self, "final_edit_store", None),
                )
            except Exception as exc:
                observe(
                    "shadow_forward_ready_package",
                    level="warning",
                    component="forward_ready_package",
                    stage="private_review_presentation_shadow",
                    status="failed",
                    error_class=type(exc).__name__,
                    mode="shadow",
                )

        if forward_ready_packages is not None:
            try:
                from .fused_private_review_authority import runtime_authority_metadata
                from .fused_private_review_delivery import (
                    plan_fused_private_review_delivery,
                    privacy_safe_observation,
                )

                fused_plans = plan_fused_private_review_delivery(
                    self.state,
                    forward_ready_packages,
                )
                for plan in fused_plans:
                    observe(
                        "shadow_fused_private_review_delivery",
                        component="fused_private_review_delivery",
                        stage="private_review_delivery_plan_shadow",
                        status="planned",
                        **privacy_safe_observation(plan),
                    )
                    # Decision-only observation. Because this occurs after legacy
                    # delivery, even an accidentally-set ON/CANARY env value cannot
                    # send a fused duplicate in this foundation. A later isolated
                    # activation phase must move an explicitly validated decision
                    # before transport and preserve receipt-aware recovery.
                    metadata = runtime_authority_metadata(
                        plan,
                        state=self.state,
                        final_edit_store=getattr(self, "final_edit_store", None),
                        review_chat_id=getattr(getattr(self, "settings", None), "review_chat_id", None),
                        receipt_stores_healthy=bool(
                            getattr(getattr(self, "telegram", None), "message_delivery_store", None) is not None
                            and getattr(self, "media_delivery", None) is not None
                        ),
                    )
                    observe(
                        "fused_private_review_authority_decision",
                        component="fused_private_review_authority",
                        stage="post_legacy_decision_foundation",
                        status="observed",
                        network_execution_enabled=False,
                        **metadata,
                    )
            except Exception as exc:
                observe(
                    "shadow_fused_private_review_delivery",
                    level="warning",
                    component="fused_private_review_delivery",
                    stage="private_review_delivery_plan_shadow",
                    status="failed",
                    error_class=type(exc).__name__,
                    mode="shadow",
                )
        return result

    deliver_updates._event_fusion_private_shadow_installed = True
    PrivateReviewApplication.deliver_updates = deliver_updates


_install()
