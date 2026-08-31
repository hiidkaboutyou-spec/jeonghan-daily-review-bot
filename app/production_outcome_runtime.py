"""Production outcome runtime — installs lifecycle hooks that build the
structured outcome during a real production run.

This module is imported by the main entrypoint.  It patches the Application
and related classes to record counters, and emits the final outcome JSON at
run completion.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state as _state_module
from .main import Application, _parse_state_datetime
from .production_outcome import (
    OutcomeBuilder,
    ProductionOutcome,
    format_log_summary,
    save_outcome,
)

logger = logging.getLogger(__name__)
_INSTALLED = False
_OUTCOME_PATH = Path("production-outcome.json")
_MODULE_BUILDER: OutcomeBuilder | None = None


def _get_builder() -> OutcomeBuilder | None:
    """Return the current module-level builder, or None if not active."""
    return _MODULE_BUILDER


def _install_source_collection_hooks() -> None:
    """Hook into scheduled scan to record source collection outcomes."""
    if Application.__dict__.get("_outcome_source_hooks_installed", False):
        return

    original_scheduled = Application.run_scheduled_scan

    async def run_scheduled_scan(self: Application) -> None:
        builder = _get_builder()
        if builder is None:
            return await original_scheduled(self)

        # Record source totals before collection
        enabled = [s for s in self.settings.sources if s.get("enabled", True)]
        disabled = [s for s in self.settings.sources if not s.get("enabled", True)]
        builder.set_source_totals(
            configured=len(self.settings.sources),
            active=len(enabled),
            disabled=len(disabled),
        )

        # Capture cursor state before collection
        before = str(self.state.data.get("last_auto_run", "") or "")

        # Record per-source outcomes from collector errors
        try:
            result = await original_scheduled(self)
        except Exception:
            raise

        # After collection, examine collector state
        collector = getattr(self, "collector", None)
        last_errors = list(getattr(collector, "last_errors", []) or [])

        # Parse source-level outcomes from errors
        attempted_handles: set[str] = set()
        for source in enabled:
            handle = str(source.get("handle", "")).lstrip("@").strip().lower()
            if handle:
                attempted_handles.add(handle)
                # Check if this handle appears in errors
                error_for_source = next(
                    (e for e in last_errors if f"@{handle}" in e or handle in e),
                    None,
                )
                if error_for_source:
                    builder.record_source_attempt(handle, complete=False, error=error_for_source)
                else:
                    builder.record_source_attempt(handle, complete=True)

        # Determine collection completeness
        if not last_errors and attempted_handles:
            builder.mark_collection_complete()

        # Record cursor state — compute from state before/after scan
        after = str(self.state.data.get("last_auto_run", "") or "")
        cursor_advanced = bool(after and after != before)
        cursor_reason = (
            "complete_window" if cursor_advanced
            else "partial_window" if last_errors
            else "not_due_or_no_advance"
        )
        builder.set_cursor(advanced=cursor_advanced, reason=cursor_reason)

        if last_errors:
            builder.set_fallback_source_count(len(last_errors))

        return result

    Application.run_scheduled_scan = run_scheduled_scan
    Application._outcome_source_hooks_installed = True


def _install_discovery_hooks() -> None:
    """Hook into queue_updates to record discovery counters."""
    if _state_module.StateStore.__dict__.get("_outcome_discovery_installed", False):
        return

    original_queue = _state_module.StateStore.queue_updates

    def queue_updates(self: _state_module.StateStore, updates, *, force: bool = False) -> None:
        builder = _get_builder()
        if builder is not None:
            # Before queuing, count what we're receiving
            before_seen = sum(1 for u in updates if self.is_seen(u.id))
            new_count = len(updates) - before_seen
            # Note: discovery counts are approximate at queue time;
            # the full picture is built from multiple collection sources.
        original_queue(self, updates, force=force)

    _state_module.StateStore.queue_updates = queue_updates
    _state_module.StateStore._outcome_discovery_installed = True


def _install_ai_hooks() -> None:
    """Hook into CaptionWriter.write_group to record AI outcomes."""
    from .ai import CaptionWriter

    if CaptionWriter.__dict__.get("_outcome_ai_installed", False):
        return

    original_write = CaptionWriter.write_group

    def write_group(self: CaptionWriter, group, *, mode: str = "default"):
        builder = _get_builder()
        result = original_write(self, group, mode=mode)

        if builder is not None:
            # Check if fallback was used (no client = fallback path)
            client = self._client_or_none()
            if client is None:
                builder.record_ai_job(success=True, fallback=True)
            else:
                # If we got here, the AI call succeeded (or fallback was used internally)
                builder.record_ai_job(
                    success=True,
                    fallback=False,
                )

            # Check for manual review items
            manual_review = getattr(self, "last_manual_review", {})
            if isinstance(manual_review, dict) and manual_review:
                builder.record_manual_review(len(manual_review))

        return result

    CaptionWriter.write_group = write_group
    CaptionWriter._outcome_ai_installed = True


def _install_telegram_hooks() -> None:
    """Hook into telegram send to record delivery outcomes."""
    from .telegram import TelegramBot

    if TelegramBot.__dict__.get("_outcome_telegram_installed", False):
        return

    original_send = TelegramBot.send_message

    def send_message(self: TelegramBot, text: str, **kwargs: Any) -> dict[str, Any]:
        builder = _get_builder()
        try:
            result = original_send(self, text, **kwargs)
            if builder is not None:
                builder.record_delivery(success=True)
            return result
        except Exception:
            if builder is not None:
                builder.record_delivery(success=False)
            raise

    TelegramBot.send_message = send_message
    TelegramBot._outcome_telegram_installed = True

    original_send_media = TelegramBot.send_media

    def send_media(self: TelegramBot, media: list) -> list[dict[str, Any]]:
        builder = _get_builder()
        try:
            result = original_send_media(self, media)
            if builder is not None:
                for _ in media:
                    builder.record_media_delivery(success=True)
            return result
        except Exception:
            if builder is not None:
                for _ in media:
                    builder.record_media_delivery(success=False)
            raise

    TelegramBot.send_media = send_media
    TelegramBot._outcome_telegram_installed = True


def _install_state_hooks() -> None:
    """Hook into state save to record checkpoint success."""
    if _state_module.StateStore.__dict__.get("_outcome_state_installed", False):
        return

    original_save = _state_module.StateStore.save

    def save(self: _state_module.StateStore) -> None:
        builder = _get_builder()
        try:
            original_save(self)
            if builder is not None:
                builder.mark_state_checkpoint(True)
        except Exception:
            if builder is not None:
                builder.mark_state_checkpoint(False)
            raise

    _state_module.StateStore.save = save
    _state_module.StateStore._outcome_state_installed = True


def _patch_application_run() -> None:
    """Wrap Application.run to build and emit the outcome."""
    if Application.__dict__.get("_outcome_run_patched", False):
        return

    original_run = Application.run

    async def run(self: Application) -> None:
        global _MODULE_BUILDER
        builder = _get_builder()
        if builder is None:
            return await original_run(self)

        try:
            result = await original_run(self)
        except Exception:
            # Even on exception, record state failure and emit what we have
            builder.mark_state_checkpoint(False)
            raise
        finally:
            # Record final backlog
            try:
                pending = len(self.state.data.get("pending_delivery", []) or [])
                builder.set_backlog(pending)
            except Exception:
                pass

            # Finalize and emit
            outcome = builder.finalize()
            try:
                save_outcome(outcome, _OUTCOME_PATH)
            except Exception as exc:
                logger.warning("Could not persist production outcome: %s", exc)

            # Log human-readable summary
            summary = format_log_summary(outcome)
            for line in summary.split("\n"):
                logger.info("OUTCOME %s", line)

        return result

    Application.run = run
    Application._outcome_run_patched = True


def install() -> None:
    """Install all production outcome hooks."""
    global _INSTALLED
    if _INSTALLED:
        return

    _install_source_collection_hooks()
    _install_discovery_hooks()
    _install_ai_hooks()
    _install_telegram_hooks()
    _install_state_hooks()
    _patch_application_run()
    _INSTALLED = True


def start_run(
    *,
    run_id: str = "",
    trigger_event: str = "",
    commit_sha: str = "",
) -> OutcomeBuilder:
    """Start a new production outcome run. Returns the builder for counter recording."""
    global _MODULE_BUILDER
    _MODULE_BUILDER = OutcomeBuilder(
        run_id=run_id,
        trigger_event=trigger_event,
        commit_sha=commit_sha,
    )
    return _MODULE_BUILDER


def finalize_run() -> ProductionOutcome | None:
    """Finalize the current run and return the outcome."""
    global _MODULE_BUILDER
    if _MODULE_BUILDER is None:
        return None
    outcome = _MODULE_BUILDER.finalize()
    try:
        save_outcome(outcome, _OUTCOME_PATH)
    except Exception as exc:
        logger.warning("Could not persist production outcome: %s", exc)
    summary = format_log_summary(outcome)
    for line in summary.split("\n"):
        logger.info("OUTCOME %s", line)
    _MODULE_BUILDER = None
    return outcome


# Auto-install on import
install()
