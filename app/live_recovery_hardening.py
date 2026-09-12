from __future__ import annotations

"""Production hardening for the Hani assistant's degraded X path.

The project intentionally keeps authenticated X as the only full-success cursor
authority. This layer makes degraded collection useful and observable without
pretending that a public fallback is a complete authenticated scan.
"""

import os
import re
from typing import Any

from . import production_outcome as _outcome
from . import production_outcome_runtime as _outcome_runtime
from . import x_provider_recovery as _provider_recovery
from . import x_syndication as _syndication
from .x_fxtwitter import FxTwitterError, collect_fxtwitter_timeline

_PROVIDER_INSTALLED = False
_CLASSIFICATION_INSTALLED = False
_SOURCE_ERROR_RE = re.compile(r"^@(?P<handle>[A-Za-z0-9_]{1,15}):\s*(?P<reason>.+)$")


def _install_public_provider_fallback() -> None:
    global _PROVIDER_INSTALLED
    if _PROVIDER_INSTALLED:
        return

    current = _provider_recovery.collect_syndication_timeline
    if getattr(current, "_hani_resilient_public_provider", False):
        _PROVIDER_INSTALLED = True
        return

    original_syndication = current

    def resilient_public_timeline(
        handle,
        start,
        end,
        *,
        include_replies: bool = True,
        timeout=(3.0, 7.0),
    ):
        try:
            return original_syndication(
                handle,
                start,
                end,
                include_replies=include_replies,
                timeout=timeout,
            )
        except _syndication.SyndicationError:
            try:
                # Bound the public fallback so a full 31-source degraded scan can
                # finish inside the GitHub Actions production window. Three pages
                # still cover up to 300 recent statuses per configured profile.
                recovered = collect_fxtwitter_timeline(
                    handle,
                    start,
                    end,
                    include_replies=include_replies,
                    timeout=(3.0, 8.0),
                    max_pages=3,
                )
            except FxTwitterError as fx_exc:
                raise _syndication.SyndicationError(
                    "public X recovery providers failed"
                ) from fx_exc
            result = _syndication.SyndicationResult(
                updates=recovered.updates,
                raw_seen=recovered.raw_seen,
            )
            return result

    resilient_public_timeline._hani_resilient_public_provider = True
    _provider_recovery.collect_syndication_timeline = resilient_public_timeline

    original_degraded = _provider_recovery.collect_degraded_window
    if not getattr(original_degraded, "_hani_degraded_tracking", False):

        async def tracked_degraded_window(
            self,
            start,
            end,
            *,
            include_sources: bool = True,
            include_keywords: bool = True,
            max_per_query: int = 60,
        ):
            selected, total = (
                _provider_recovery._select_rotating_batch(self)
                if include_sources
                else ([], 0)
            )
            attempted = [
                str(source.get("handle", "")).lstrip("@").strip().lower()
                for source in selected
                if str(source.get("handle", "")).lstrip("@").strip()
            ]
            self._hani_degraded_attempted_sources = attempted
            self._hani_degraded_total_sources = total
            self._hani_degraded_failed_sources = {}
            self._hani_degraded_successful_sources = []

            try:
                return await original_degraded(
                    self,
                    start,
                    end,
                    include_sources=include_sources,
                    include_keywords=include_keywords,
                    max_per_query=max_per_query,
                )
            finally:
                failed: dict[str, str] = {}
                for raw_error in list(getattr(self, "last_errors", []) or []):
                    match = _SOURCE_ERROR_RE.match(str(raw_error).strip())
                    if match is None:
                        continue
                    handle = match.group("handle").casefold()
                    if handle in attempted:
                        failed[handle] = match.group("reason").strip()
                self._hani_degraded_failed_sources = failed
                self._hani_degraded_successful_sources = [
                    handle for handle in attempted if handle not in failed
                ]

        tracked_degraded_window._hani_degraded_tracking = True
        _provider_recovery.collect_degraded_window = tracked_degraded_window

    _PROVIDER_INSTALLED = True


def _install_outcome_classification() -> None:
    global _CLASSIFICATION_INSTALLED
    if _CLASSIFICATION_INSTALLED:
        return

    current = _outcome.classify_outcome
    if getattr(current, "_hani_collection_hardening", False):
        _CLASSIFICATION_INSTALLED = True
        return

    original_classify = current

    def classify_outcome_hardened(outcome):
        status, reasons = original_classify(outcome)
        if status == _outcome.OutcomeStatus.FAILED.value:
            return status, reasons

        sc = outcome.source_collection
        state = outcome.state
        coverage_gap = (
            sc.active_source_count > 0
            and sc.attempted_source_count < sc.active_source_count
        )
        incomplete_evidence = (
            not sc.collection_complete
            and (
                sc.partial_source_count > 0
                or sc.failed_source_count > 0
                or coverage_gap
            )
        )
        if not incomplete_evidence:
            return status, reasons

        if not state.cursor_advanced:
            hardened = list(reasons)
            if "incomplete_collection_cursor_held" not in hardened:
                hardened.append("incomplete_collection_cursor_held")
            return _outcome.OutcomeStatus.RECOVERY_REQUIRED.value, hardened

        if status == _outcome.OutcomeStatus.HEALTHY.value:
            return (
                _outcome.OutcomeStatus.DEGRADED.value,
                ["incomplete_source_collection"],
            )
        return status, reasons

    classify_outcome_hardened._hani_collection_hardening = True
    _outcome.classify_outcome = classify_outcome_hardened
    _CLASSIFICATION_INSTALLED = True


def _reconcile_degraded_outcome(application: Any) -> None:
    if os.environ.get("X_PROVIDER_PREFLIGHT", "").strip().casefold() != "degraded":
        return

    builder = _outcome_runtime._get_builder()
    collector = getattr(application, "collector", None)
    if builder is None or collector is None:
        return

    attempted = list(getattr(collector, "_hani_degraded_attempted_sources", []) or [])
    if not attempted:
        return
    failed = dict(getattr(collector, "_hani_degraded_failed_sources", {}) or {})

    sc = builder.outcome.source_collection

    # production_outcome_runtime historically inferred every enabled source as
    # attempted. Replace those inferred counters with the degraded provider's
    # actual rotating batch.
    sc.attempted_source_count = 0
    sc.complete_source_count = 0
    sc.partial_source_count = 0
    sc.failed_source_count = 0
    sc.failed_source_handles = []
    sc.failed_source_reasons = []
    sc.collection_complete = False

    for handle in attempted:
        reason = failed.get(handle)
        if reason:
            builder.record_source_attempt(handle, complete=False, error=reason)
        else:
            # Public-provider success is useful but not authoritative enough to
            # advance the authenticated full-success cursor.
            builder.record_source_attempt(handle, complete=False)

    builder.set_fallback_source_count(len(attempted))
    builder.set_recovery(
        required=True,
        reason="x_provider_degraded_partial_collection",
        dispatch_recommended=False,
    )


def install(application_cls: type[Any]) -> None:
    """Install live recovery hardening on the final production application."""

    _install_public_provider_fallback()
    _install_outcome_classification()

    if application_cls.__dict__.get("_hani_live_recovery_hardening", False):
        return

    original_scan = application_cls.run_scheduled_scan

    async def run_scheduled_scan(self):
        result = await original_scan(self)
        _reconcile_degraded_outcome(self)
        return result

    application_cls.run_scheduled_scan = run_scheduled_scan
    application_cls._hani_live_recovery_hardening = True
