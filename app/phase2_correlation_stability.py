"""Freeze Phase 2 correlation identifiers once an update enters its lifecycle."""

from __future__ import annotations

from . import zero_silent_miss as _zsm
from .state import StateStore


def _stable_translation_job_id(event_id: str, update_id: str) -> str:
    # A translation job belongs to the stable source update. Event labels may be
    # normalized differently by grouping/draft code and retries may use new attempt
    # IDs, but neither is allowed to change the logical translation correlation ID.
    return _zsm._stable_id("tr", update_id)


def _install_stable_lifecycle_ids() -> None:
    current = _zsm._record_update_state
    if getattr(current, "_phase2_correlation_stable", False):
        return

    def record(self, update_or_id, **kwargs):
        existing = _zsm._lifecycle_record(self, update_or_id)
        existing_event = str(existing.get("event_id", "") or "")
        existing_translation = str(existing.get("translation_job_id", "") or "")
        if existing_event:
            kwargs["event_id"] = existing_event
        if existing_translation:
            kwargs["translation_job_id"] = existing_translation
        return current(self, update_or_id, **kwargs)

    record._phase2_correlation_stable = True
    _zsm._record_update_state = record
    StateStore.record_update_state = record
    _zsm.translation_job_id = _stable_translation_job_id


def install() -> None:
    _install_stable_lifecycle_ids()


install()
