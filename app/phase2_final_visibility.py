"""Small final Phase 2 visibility hooks that require underlying runtime reports."""

from __future__ import annotations

from .media_delivery_runtime import MediaDedupReviewApplication
from .observability import observe
from .state import StateStore


def _install_partial_media_visibility() -> None:
    current = MediaDedupReviewApplication._deliver_private_media
    if getattr(current, "_phase2_partial_media_visible", False):
        return

    async def deliver(self, update):
        result = await current(self, update)
        report = getattr(self, "_last_media_delivery_report", None)
        if not isinstance(report, dict):
            return result
        try:
            failed = max(0, int(report.get("failed", 0) or 0))
            sent = max(0, int(report.get("sent", 0) or 0))
            requested = max(0, int(report.get("requested", 0) or 0))
        except (TypeError, ValueError):
            return result
        if result is True and failed > 0:
            state = getattr(self, "state", None)
            recorder = getattr(state, "record_update_state", None) if state is not None else None
            if callable(recorder):
                current_state = state.get_update_state(update.id) or {}
                recorder(
                    update,
                    status="pending_delivery",
                    stage="telegram_delivery",
                    media_status="partial_failed",
                    media_asset_ids=str(current_state.get("media_asset_ids", "")),
                    reason="media_partial_failure",
                )
            observe(
                "telegram_media_delivery_partial",
                level="warning",
                stage="media",
                status="partial_failed",
                update_id=update.id,
                source=update.author,
                post_id=update.id,
                media_count=requested,
                prepared_count=sent,
            )
        return result

    deliver._phase2_partial_media_visible = True
    MediaDedupReviewApplication._deliver_private_media = deliver


def _install_fidelity_result_visibility() -> None:
    current = StateStore.save_draft
    if getattr(current, "_phase2_fidelity_visible", False):
        return

    def save_draft(self, draft):
        current(self, draft)
        if getattr(draft, "mode", "") != "manual_review":
            return
        lifecycle = self.get_update_state(draft.update_id) or {}
        self.record_update_state(
            draft.update_id,
            status="pending_delivery",
            stage="telegram_delivery",
            event_id=str(lifecycle.get("event_id") or draft.event_key),
            retrieval_attempt_id=str(lifecycle.get("retrieval_attempt_id", "")),
            retrieval_status=str(lifecycle.get("retrieval_status", "")),
            media_status=str(lifecycle.get("media_status", "")),
            translation_status="fidelity_rejected",
            translation_job_id=str(lifecycle.get("translation_job_id", "")),
            delivery_key=str(lifecycle.get("delivery_key") or f"draft:{draft.id}"),
            reason="manual_review_required",
        )
        observe(
            "translation_fidelity_rejected",
            level="warning",
            stage="translation",
            status="fidelity_rejected",
            update_id=draft.update_id,
            event_id=draft.event_key,
            translation_job_id=str(lifecycle.get("translation_job_id", "")),
        )

    save_draft._phase2_fidelity_visible = True
    StateStore.save_draft = save_draft


def install() -> None:
    _install_partial_media_visibility()
    _install_fidelity_result_visibility()


install()
