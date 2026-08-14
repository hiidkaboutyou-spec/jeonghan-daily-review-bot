"""Phase 2 observability and zero-silent-miss lifecycle instrumentation.

This module is deliberately installed *after* the configured-source Phase 1 guards.
It observes those exact retrieval/delivery semantics without widening source scope,
changing grouping, or adding a new delivery transport.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from . import configured_source_runtime as _configured_runtime
from . import source_authority_hardening as _source_authority
from . import state as _state_module
from .main import Application
from .media import MediaManager
from .media_delivery_runtime import MediaDedupReviewApplication
from .models import Draft, Update
from .observability import (
    current_retrieval_attempt_id,
    new_attempt_id,
    observe,
    reset_retrieval_attempt,
    set_retrieval_attempt,
)
from .organizer import organize_updates
from .private_runtime import PrivateReviewApplication
from .state import StateStore
from .translation_safety import translation_unavailable
from .x_client import XCollector
from .x_completeness import CompleteWindowXCollector

logger = logging.getLogger(__name__)
_INSTALLED = False
_LAST_RETRIEVAL_BY_UPDATE: dict[str, dict[str, str]] = {}
_LIFECYCLE_LIMIT = 30000
_QUARANTINE_LIMIT = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def translation_job_id(event_id: str, update_id: str) -> str:
    return _stable_id("tr", event_id, update_id)


def media_asset_id(kind: str, url: str) -> str:
    # The source URL itself is intentionally never emitted to observability.
    return _stable_id("media", kind, url)


def _safe_code(value: object, default: str = "unknown") -> str:
    text = str(value or default).strip()
    allowed = "".join(ch for ch in text if ch.isalnum() or ch in "._:-")[:120]
    return allowed or default


def _lifecycle_record(self: StateStore, update_or_id: Update | str) -> dict[str, Any]:
    update_id = update_or_id.id if isinstance(update_or_id, Update) else str(update_or_id)
    raw = self.data.setdefault("update_lifecycle", {}).get(update_id)
    return dict(raw) if isinstance(raw, dict) else {}


def _record_update_state(
    self: StateStore,
    update_or_id: Update | str,
    *,
    status: str,
    stage: str,
    source: str = "",
    event_id: str = "",
    retrieval_attempt_id: str = "",
    retrieval_status: str = "",
    media_status: str = "",
    media_asset_ids: str = "",
    translation_status: str = "",
    translation_job_id: str = "",
    delivery_key: str = "",
    delivery_receipt_id: int | str = 0,
    error_class: str = "",
    reason: str = "",
    retry_count: int | None = None,
) -> dict[str, Any]:
    update = update_or_id if isinstance(update_or_id, Update) else None
    update_id = update.id if update is not None else str(update_or_id)
    current = _lifecycle_record(self, update_id)
    current.setdefault("update_id", update_id)
    current.setdefault("post_id", update_id)
    if update is not None:
        current["source"] = update.author
    elif source:
        current["source"] = str(source).lstrip("@")[:80]
    if event_id:
        current["event_id"] = str(event_id)[:160]
    if retrieval_attempt_id:
        current["retrieval_attempt_id"] = str(retrieval_attempt_id)[:80]
    if retrieval_status:
        current["retrieval_status"] = _safe_code(retrieval_status)
    if media_status:
        current["media_status"] = _safe_code(media_status)
    if media_asset_ids:
        current["media_asset_ids"] = str(media_asset_ids)[:600]
    if translation_status:
        current["translation_status"] = _safe_code(translation_status)
    if translation_job_id:
        current["translation_job_id"] = str(translation_job_id)[:120]
    if delivery_key:
        current["delivery_key"] = str(delivery_key)[:180]
    if delivery_receipt_id:
        try:
            current["delivery_receipt_id"] = max(0, int(delivery_receipt_id))
        except (TypeError, ValueError):
            current["delivery_receipt_id"] = 0
    if error_class:
        current["error_class"] = _safe_code(error_class, "Error")
    elif status in {"delivered", "delivered_text_media_failed"}:
        current["error_class"] = ""
    if reason:
        current["reason"] = _safe_code(reason)
    elif status == "delivered":
        current["reason"] = ""
    previous_retry = current.get("retry_count", 0)
    try:
        previous_retry = max(0, int(previous_retry or 0))
    except (TypeError, ValueError):
        previous_retry = 0
    if retry_count is not None:
        current["retry_count"] = max(0, int(retry_count))
    elif status == "retry_pending":
        current["retry_count"] = min(previous_retry + 1, 1000)
    else:
        current.setdefault("retry_count", previous_retry)
    current["status"] = _safe_code(status)
    current["stage"] = _safe_code(stage)
    current["updated_at"] = _now()
    self.data.setdefault("update_lifecycle", {})[update_id] = current

    level = "warning" if status in {
        "retry_pending",
        "pending_translation",
        "partial_source_window",
        "quarantined_with_reason",
        "delivered_text_media_failed",
        "terminal_failed_with_reason",
    } else "info"
    observe(
        "update_lifecycle",
        level=level,
        stage=current.get("stage", ""),
        status=current.get("status", ""),
        update_id=update_id,
        source=current.get("source", ""),
        post_id=update_id,
        event_id=current.get("event_id", ""),
        retrieval_attempt_id=current.get("retrieval_attempt_id", ""),
        media_asset_id=current.get("media_asset_ids", ""),
        translation_job_id=current.get("translation_job_id", ""),
        delivery_key=current.get("delivery_key", ""),
        delivery_receipt_id=current.get("delivery_receipt_id", 0),
        error_class=current.get("error_class", ""),
        retry_count=current.get("retry_count", 0),
    )
    return current


def _get_update_state(self: StateStore, update_id: str) -> dict[str, Any] | None:
    raw = self.data.setdefault("update_lifecycle", {}).get(str(update_id))
    return dict(raw) if isinstance(raw, dict) else None


def _quarantine_pending(
    self: StateStore,
    raw: Any,
    *,
    reason: str,
    update_id: str = "",
    source: str = "",
) -> None:
    entry: dict[str, Any] = {
        "quarantined_at": _now(),
        "reason": _safe_code(reason),
        "update_id": str(update_id)[:120],
        "source": str(source).lstrip("@")[:80],
    }
    # Keep the original private durable payload locally when it is JSON-safe so an
    # operator can investigate/recover it. It is never passed to observe()/Sentry.
    if isinstance(raw, dict):
        entry["payload"] = raw
    else:
        entry["payload_type"] = type(raw).__name__
    queue = self.data.setdefault("quarantined_delivery", [])
    queue.append(entry)
    del queue[:-_QUARANTINE_LIMIT]
    correlation_id = str(update_id or _stable_id("quarantine", json.dumps(entry, sort_keys=True, default=str)))
    _record_update_state(
        self,
        correlation_id,
        status="quarantined_with_reason",
        stage="queue",
        source=source,
        reason=reason,
    )


def _install_state_lifecycle() -> None:
    if StateStore.__dict__.get("_zero_silent_miss_installed", False):
        return
    _state_module.SCHEMA_VERSION = max(5, int(_state_module.SCHEMA_VERSION))
    original_fresh = StateStore._fresh
    original_normalize = StateStore._normalize_loaded
    original_queue_updates = StateStore.queue_updates
    original_save_draft = StateStore.save_draft
    original_mark_seen = StateStore.mark_seen
    original_prune = StateStore.prune

    def fresh(self):
        data = original_fresh(self)
        data.setdefault("update_lifecycle", {})
        data.setdefault("quarantined_delivery", [])
        data["schema"] = _state_module.SCHEMA_VERSION
        return data

    def normalize(self, value):
        data = original_normalize(self, value)
        lifecycle = value.get("update_lifecycle") if isinstance(value, dict) else None
        if isinstance(lifecycle, dict):
            clean: dict[str, dict[str, Any]] = {}
            for key, raw in list(lifecycle.items())[-_LIFECYCLE_LIMIT:]:
                if not isinstance(raw, dict):
                    continue
                record = {
                    field: raw[field]
                    for field in (
                        "update_id", "post_id", "source", "event_id", "status", "stage",
                        "retrieval_attempt_id", "retrieval_status", "media_status", "media_asset_ids",
                        "translation_status", "translation_job_id", "delivery_key",
                        "delivery_receipt_id", "error_class", "reason", "retry_count", "updated_at",
                    )
                    if field in raw
                }
                clean[str(key)] = record
            data["update_lifecycle"] = clean
        quarantined = value.get("quarantined_delivery") if isinstance(value, dict) else None
        if isinstance(quarantined, list):
            data["quarantined_delivery"] = [item for item in quarantined[-_QUARANTINE_LIMIT:] if isinstance(item, dict)]
        data["schema"] = _state_module.SCHEMA_VERSION
        return data

    def queue_updates(self, updates: list[Update], *, force: bool = False) -> None:
        original_queue_updates(self, updates, force=force)
        for update in updates:
            if self.is_seen(update.id):
                continue
            retrieval = _LAST_RETRIEVAL_BY_UPDATE.get(update.id, {})
            _record_update_state(
                self,
                update,
                status="pending_delivery",
                stage="queue",
                retrieval_attempt_id=retrieval.get("retrieval_attempt_id", ""),
                retrieval_status=retrieval.get("retrieval_status", ""),
            )

    def pop_pending(self, limit: int) -> list[tuple[Update, bool]]:
        """Peek pending rows, quarantining malformed payloads instead of losing them."""
        queue = list(self.data.get("pending_delivery", []))
        remaining: list[dict[str, Any]] = []
        result: list[tuple[Update, bool]] = []
        limit = max(0, int(limit))
        for item in queue:
            if not isinstance(item, dict):
                _quarantine_pending(self, item, reason="invalid_pending_row_type")
                continue
            update_id = str(item.get("id", ""))
            source = str(item.get("author", "") or "")
            if update_id and self.is_seen(update_id):
                continue
            payload = dict(item)
            force = bool(payload.pop("force", False))
            try:
                update = Update.from_dict(payload)
            except (TypeError, ValueError):
                _quarantine_pending(
                    self,
                    item,
                    reason="invalid_pending_payload",
                    update_id=update_id,
                    source=source,
                )
                continue
            remaining.append(item)
            if len(result) < limit:
                result.append((update, force))
                current = _lifecycle_record(self, update)
                _record_update_state(
                    self,
                    update,
                    status="pending_delivery",
                    stage="queue",
                    retrieval_attempt_id=str(current.get("retrieval_attempt_id", "")),
                    retrieval_status=str(current.get("retrieval_status", "")),
                )
        self.data["pending_delivery"] = remaining
        return result

    def save_draft(self, draft: Draft) -> None:
        original_save_draft(self, draft)
        unavailable = translation_unavailable(draft.caption)
        _record_update_state(
            self,
            draft.update_id,
            status="pending_translation" if unavailable else "pending_delivery",
            stage="translation" if unavailable else "telegram_delivery",
            event_id=draft.event_key,
            translation_status="provider_unavailable" if unavailable else "success",
            translation_job_id=translation_job_id(draft.event_key, draft.update_id),
            delivery_key=f"draft:{draft.id}",
            reason="provider_unavailable" if unavailable else "",
        )

    def mark_seen(self, update: Update) -> None:
        current = _lifecycle_record(self, update)
        drafts = [
            raw for raw in self.data.get("drafts", {}).values()
            if isinstance(raw, dict) and str(raw.get("update_id", "")) == update.id
        ]
        drafts.sort(key=lambda raw: str(raw.get("created_at", "")))
        receipt = 0
        delivery_key = str(current.get("delivery_key", ""))
        event_id = str(current.get("event_id", ""))
        if drafts:
            latest = drafts[-1]
            try:
                receipt = int(latest.get("telegram_message_id", 0) or 0)
            except (TypeError, ValueError):
                receipt = 0
            if not delivery_key and latest.get("id"):
                delivery_key = f"draft:{latest['id']}"
            event_id = event_id or str(latest.get("event_key", ""))
        media_status = str(current.get("media_status", ""))
        final_status = "delivered_text_media_failed" if media_status in {"terminal_failed", "partial_failed"} else "delivered"
        original_mark_seen(self, update)
        _record_update_state(
            self,
            update,
            status=final_status,
            stage="telegram_delivery",
            event_id=event_id,
            media_status=media_status,
            translation_status=str(current.get("translation_status", "")) or "success",
            translation_job_id=str(current.get("translation_job_id", "")),
            delivery_key=delivery_key,
            delivery_receipt_id=receipt,
            reason="media_unavailable" if final_status != "delivered" else "",
        )

    def prune(self) -> None:
        original_prune(self)
        lifecycle = self.data.get("update_lifecycle", {})
        if isinstance(lifecycle, dict) and len(lifecycle) > _LIFECYCLE_LIMIT:
            ordered = sorted(
                ((key, value) for key, value in lifecycle.items() if isinstance(value, dict)),
                key=lambda pair: str(pair[1].get("updated_at", "")),
                reverse=True,
            )[:_LIFECYCLE_LIMIT]
            self.data["update_lifecycle"] = dict(ordered)
        quarantined = self.data.get("quarantined_delivery", [])
        if isinstance(quarantined, list) and len(quarantined) > _QUARANTINE_LIMIT:
            self.data["quarantined_delivery"] = quarantined[-_QUARANTINE_LIMIT:]

    StateStore._fresh = fresh
    StateStore._normalize_loaded = normalize
    StateStore.queue_updates = queue_updates
    StateStore.pop_pending = pop_pending
    StateStore.save_draft = save_draft
    StateStore.mark_seen = mark_seen
    StateStore.prune = prune
    StateStore.record_update_state = _record_update_state
    StateStore.get_update_state = _get_update_state
    StateStore.quarantine_pending = _quarantine_pending
    StateStore._zero_silent_miss_installed = True


def _install_filter_visibility() -> None:
    original = _source_authority._configured_only
    if getattr(original, "_phase2_observed", False):
        return

    def configured_only(self: XCollector, updates: Iterable[Update]) -> list[Update]:
        raw = list(updates)
        configured = _source_authority._configured_handles(self)
        configured_raw = [item for item in raw if item.author.casefold() in configured]
        external_dropped = len(raw) - len(configured_raw)
        duplicate_dropped = len(configured_raw) - len({item.id for item in configured_raw})
        result = original(self, raw)
        observe(
            "retrieval_filter_dedupe",
            stage="filter_dedupe",
            status="complete",
            retrieval_attempt_id=current_retrieval_attempt_id(),
            discovered=len(raw),
            retained=len(result),
            external_dropped=external_dropped,
            duplicate_dropped=duplicate_dropped,
        )
        return result

    configured_only._phase2_observed = True
    _source_authority._configured_only = configured_only
    XCollector.filter_configured_updates = configured_only


def _remember_retrieval(updates: Iterable[Update], attempt_id: str, status: str) -> None:
    for update in updates:
        _LAST_RETRIEVAL_BY_UPDATE[update.id] = {
            "retrieval_attempt_id": attempt_id,
            "retrieval_status": status,
        }
    if len(_LAST_RETRIEVAL_BY_UPDATE) > 10000:
        for key in list(_LAST_RETRIEVAL_BY_UPDATE)[:5000]:
            _LAST_RETRIEVAL_BY_UPDATE.pop(key, None)


def _install_retrieval_visibility() -> None:
    if XCollector.__dict__.get("_phase2_retrieval_observed", False):
        return
    original_window = XCollector.collect_window
    original_source = XCollector.collect_source
    original_search = XCollector.search_archive
    original_event = XCollector.collect_event

    async def collect_window(self, start, end, **kwargs):
        attempt = new_attempt_id()
        token = set_retrieval_attempt(attempt)
        observe(
            "retrieval_window_start",
            stage="retrieval",
            status="started",
            retrieval_attempt_id=attempt,
            pagination="provider_managed",
            pages_requested="provider_managed",
            cursor_requested="provider_managed",
        )
        try:
            result = await original_window(self, start, end, **kwargs)
        except Exception as exc:
            observe(
                "retrieval_window_end",
                level="error",
                stage="retrieval",
                status="failed",
                retrieval_attempt_id=attempt,
                error_class=type(exc).__name__,
                complete=False,
            )
            raise
        finally:
            reset_retrieval_attempt(token)
        partial = bool(getattr(self, "last_errors", []))
        status = "partial_source_window" if partial else "complete"
        _remember_retrieval(result, attempt, status)
        self.last_retrieval_attempt_id = attempt
        self.last_retrieval_status = status
        observe(
            "retrieval_window_end",
            level="warning" if partial else "info",
            stage="retrieval",
            status=status,
            retrieval_attempt_id=attempt,
            complete=not partial,
            partial=partial,
            retained=len(result),
        )
        return result

    async def collect_source(self, handle, start, end):
        attempt = new_attempt_id()
        token = set_retrieval_attempt(attempt)
        observe(
            "retrieval_source_start",
            stage="retrieval",
            status="started",
            source=str(handle).lstrip("@"),
            retrieval_attempt_id=attempt,
            pagination="provider_managed",
            pages_requested="provider_managed",
            cursor_requested="provider_managed",
        )
        try:
            result = await original_source(self, handle, start, end)
        except Exception as exc:
            observe(
                "retrieval_source_end",
                level="error",
                stage="retrieval",
                status="failed",
                source=str(handle).lstrip("@"),
                retrieval_attempt_id=attempt,
                error_class=type(exc).__name__,
                complete=False,
            )
            raise
        finally:
            reset_retrieval_attempt(token)
        _remember_retrieval(result, attempt, "complete")
        self.last_retrieval_attempt_id = attempt
        self.last_retrieval_status = "complete"
        observe(
            "retrieval_source_end",
            stage="retrieval",
            status="complete",
            source=str(handle).lstrip("@"),
            retrieval_attempt_id=attempt,
            complete=True,
            retained=len(result),
        )
        return result

    async def search_archive(self, queries, **kwargs):
        attempt = new_attempt_id()
        token = set_retrieval_attempt(attempt)
        observe("retrieval_search_start", stage="retrieval", status="started", retrieval_attempt_id=attempt)
        try:
            result = await original_search(self, queries, **kwargs)
        except Exception as exc:
            observe(
                "retrieval_search_end",
                level="error",
                stage="retrieval",
                status="failed",
                retrieval_attempt_id=attempt,
                error_class=type(exc).__name__,
            )
            raise
        finally:
            reset_retrieval_attempt(token)
        partial = bool(getattr(self, "last_errors", []))
        status = "partial_source_window" if partial else "complete"
        _remember_retrieval(result, attempt, status)
        observe(
            "retrieval_search_end",
            level="warning" if partial else "info",
            stage="retrieval",
            status=status,
            retrieval_attempt_id=attempt,
            complete=not partial,
            retained=len(result),
        )
        return result

    async def collect_event(self, selected):
        attempt = new_attempt_id()
        token = set_retrieval_attempt(attempt)
        observe(
            "retrieval_event_start",
            stage="retrieval",
            status="started",
            update_id=selected.id,
            source=selected.author,
            post_id=selected.id,
            retrieval_attempt_id=attempt,
        )
        try:
            result = await original_event(self, selected)
        except Exception as exc:
            observe(
                "retrieval_event_end",
                level="error",
                stage="retrieval",
                status="failed",
                update_id=selected.id,
                source=selected.author,
                post_id=selected.id,
                retrieval_attempt_id=attempt,
                error_class=type(exc).__name__,
            )
            raise
        finally:
            reset_retrieval_attempt(token)
        partial = bool(getattr(self, "last_errors", []))
        status = "partial_source_window" if partial else "complete"
        _remember_retrieval(result, attempt, status)
        observe(
            "retrieval_event_end",
            level="warning" if partial else "info",
            stage="retrieval",
            status=status,
            update_id=selected.id,
            source=selected.author,
            post_id=selected.id,
            retrieval_attempt_id=attempt,
            complete=not partial,
            retained=len(result),
        )
        return result

    XCollector.collect_window = collect_window
    XCollector.collect_source = collect_source
    XCollector.search_archive = search_archive
    XCollector.collect_event = collect_event
    # Phase 1 intentionally binds this subclass explicitly; keep the same source
    # policy while adding the common visibility wrapper.
    CompleteWindowXCollector.collect_source = collect_source
    XCollector._phase2_retrieval_observed = True


def _install_media_prepare_visibility() -> None:
    if MediaManager.__dict__.get("_phase2_observed", False):
        return
    original = MediaManager.prepare

    def prepare(self: MediaManager, update: Update):
        asset_ids = [media_asset_id(item.kind, item.url) for item in update.media[:20]]
        observe(
            "media_prepare_start",
            stage="media",
            status="started",
            update_id=update.id,
            source=update.author,
            post_id=update.id,
            media_asset_id=",".join(asset_ids),
            media_count=len(asset_ids),
        )
        try:
            temp, prepared = original(self, update)
        except Exception as exc:
            observe(
                "media_prepare_end",
                level="error",
                stage="media",
                status="failed",
                update_id=update.id,
                source=update.author,
                post_id=update.id,
                media_asset_id=",".join(asset_ids),
                error_class=type(exc).__name__,
            )
            raise
        methods = ",".join(str(item.metadata.get("retrieval_method", "unknown")) for item in prepared)
        partial = len(prepared) < len(asset_ids)
        observe(
            "media_prepare_end",
            level="warning" if partial else "info",
            stage="media",
            status="partial" if partial else "complete",
            update_id=update.id,
            source=update.author,
            post_id=update.id,
            media_asset_id=",".join(asset_ids),
            media_count=len(asset_ids),
            prepared_count=len(prepared),
            retrieval_method=methods,
            fallback=any(method not in {"direct", "orig", "4096x4096"} for method in methods.split(",") if method),
        )
        return temp, prepared

    MediaManager.prepare = prepare
    MediaManager._phase2_observed = True


def _wrap_media_delivery(cls) -> None:
    if cls.__dict__.get("_phase2_media_delivery_observed", False):
        return
    original = cls._deliver_private_media

    async def deliver(self, update: Update) -> bool:
        asset_ids = ",".join(media_asset_id(item.kind, item.url) for item in update.media[:20])
        self.state.record_update_state(
            update,
            status="pending_media",
            stage="media",
            media_status="pending",
            media_asset_ids=asset_ids,
        )
        observe(
            "telegram_media_delivery_start",
            stage="media",
            status="started",
            update_id=update.id,
            source=update.author,
            post_id=update.id,
            media_asset_id=asset_ids,
            media_count=len(update.media[:20]),
        )
        try:
            result = await original(self, update)
        except Exception as exc:
            self.state.record_update_state(
                update,
                status="retry_pending",
                stage="media",
                media_status="retry_pending",
                media_asset_ids=asset_ids,
                error_class=type(exc).__name__,
                reason="media_delivery_exception",
            )
            observe(
                "telegram_media_delivery_end",
                level="error",
                stage="media",
                status="retry_pending",
                update_id=update.id,
                source=update.author,
                post_id=update.id,
                media_asset_id=asset_ids,
                error_class=type(exc).__name__,
            )
            raise
        if result is False:
            self.state.record_update_state(
                update,
                status="pending_delivery",
                stage="telegram_delivery",
                media_status="terminal_failed",
                media_asset_ids=asset_ids,
                reason="media_unavailable",
            )
            observe(
                "telegram_media_delivery_end",
                level="warning",
                stage="media",
                status="terminal_failed_with_reason",
                update_id=update.id,
                source=update.author,
                post_id=update.id,
                media_asset_id=asset_ids,
            )
        else:
            self.state.record_update_state(
                update,
                status="pending_delivery",
                stage="telegram_delivery",
                media_status="delivered",
                media_asset_ids=asset_ids,
            )
            observe(
                "telegram_media_delivery_end",
                stage="media",
                status="delivered",
                update_id=update.id,
                source=update.author,
                post_id=update.id,
                media_asset_id=asset_ids,
            )
        return result

    cls._deliver_private_media = deliver
    cls._phase2_media_delivery_observed = True


def _install_delivery_visibility() -> None:
    if PrivateReviewApplication.__dict__.get("_phase2_delivery_observed", False):
        return
    original = PrivateReviewApplication.deliver_updates

    async def deliver_updates(self, updates: list[Update], *, force: bool):
        safe = list(updates)
        collector = getattr(self, "collector", None)
        filter_fn = getattr(collector, "filter_configured_updates", None)
        if callable(filter_fn):
            safe = filter_fn(safe)
        if not safe:
            return await original(self, safe, force=force)
        groups = organize_updates(safe)
        event_by_update: dict[str, str] = {}
        for group in groups:
            for update in group.updates:
                event_by_update[update.id] = group.key
        observe(
            "grouping_complete",
            stage="grouping",
            status="complete",
            discovered=len(safe),
            retained=len(groups),
        )
        for update in safe:
            event_id = event_by_update.get(update.id, update.event_key or f"single:{update.id}")
            retrieval = _LAST_RETRIEVAL_BY_UPDATE.get(update.id, {})
            self.state.record_update_state(
                update,
                status="pending_translation",
                stage="translation",
                event_id=event_id,
                retrieval_attempt_id=retrieval.get("retrieval_attempt_id", ""),
                retrieval_status=retrieval.get("retrieval_status", ""),
                translation_status="requested",
                translation_job_id=translation_job_id(event_id, update.id),
            )
            observe(
                "translation_requested",
                stage="translation",
                status="started",
                update_id=update.id,
                source=update.author,
                post_id=update.id,
                event_id=event_id,
                retrieval_attempt_id=retrieval.get("retrieval_attempt_id", ""),
                translation_job_id=translation_job_id(event_id, update.id),
                provider="gemini",
                model=str(getattr(self.settings, "gemini_model", "")),
            )
        try:
            result = await original(self, safe, force=force)
        except Exception as exc:
            # Delivery is sequential. The first unseen item is the work that did not
            # receive a confirmed final text receipt; prior items have already been
            # checkpointed by mark_seen()/MessageDeliveryStore.
            failed = next((item for item in safe if not self.state.is_seen(item.id)), None)
            if failed is not None:
                current = self.state.get_update_state(failed.id) or {}
                if current.get("status") != "retry_pending":
                    self.state.record_update_state(
                        failed,
                        status="retry_pending",
                        stage=str(current.get("stage") or "translation"),
                        event_id=str(current.get("event_id") or event_by_update.get(failed.id, "")),
                        media_status=str(current.get("media_status", "")),
                        translation_status=str(current.get("translation_status", "")),
                        translation_job_id=str(current.get("translation_job_id", "")),
                        delivery_key=str(current.get("delivery_key", "")),
                        error_class=type(exc).__name__,
                        reason="stage_exception",
                    )
            raise
        for update in safe:
            if self.state.is_seen(update.id):
                continue
            current = self.state.get_update_state(update.id) or {}
            # A normal return with an unseen item is the established translation
            # outage/defer path. Keep the durable pending row and explain why.
            if current.get("stage") == "translation" or current.get("status") == "pending_translation":
                circuit = str(getattr(self.writer, "_gemini_circuit_open", "") or "")
                reason = "quota_429" if circuit == "quota" else "provider_unavailable"
                self.state.record_update_state(
                    update,
                    status="pending_translation",
                    stage="translation",
                    event_id=str(current.get("event_id") or event_by_update.get(update.id, "")),
                    retrieval_attempt_id=str(current.get("retrieval_attempt_id", "")),
                    retrieval_status=str(current.get("retrieval_status", "")),
                    translation_status=reason,
                    translation_job_id=str(current.get("translation_job_id", "")),
                    reason=reason,
                )
                observe(
                    "translation_deferred",
                    level="warning",
                    stage="translation",
                    status="pending_translation",
                    update_id=update.id,
                    source=update.author,
                    post_id=update.id,
                    event_id=str(current.get("event_id", "")),
                    translation_job_id=str(current.get("translation_job_id", "")),
                    provider="gemini",
                    model=str(getattr(self.settings, "gemini_model", "")),
                    error_class="RateLimit" if reason == "quota_429" else "ProviderUnavailable",
                )
        return result

    PrivateReviewApplication.deliver_updates = deliver_updates
    PrivateReviewApplication._phase2_delivery_observed = True
    _wrap_media_delivery(PrivateReviewApplication)
    _wrap_media_delivery(MediaDedupReviewApplication)


def _install_cursor_visibility() -> None:
    def wrap(cls) -> None:
        if cls.__dict__.get("_phase2_cursor_observed", False):
            return
        original = cls.run_scheduled_scan

        async def run_scheduled_scan(self):
            before = str(self.state.data.get("last_auto_run", "") or "")
            try:
                result = await original(self)
            except Exception as exc:
                observe(
                    "cursor_decision",
                    level="error",
                    stage="state",
                    status="failed",
                    cursor_advanced=False,
                    cursor_reason="scan_exception",
                    error_class=type(exc).__name__,
                )
                raise
            after = str(self.state.data.get("last_auto_run", "") or "")
            errors = bool(getattr(getattr(self, "collector", None), "last_errors", []))
            advanced = bool(after and after != before)
            reason = "complete_window" if advanced else "partial_window" if errors else "not_due_or_no_advance"
            observe(
                "cursor_decision",
                level="warning" if errors else "info",
                stage="state",
                status="complete" if advanced else "retained",
                retrieval_attempt_id=str(getattr(getattr(self, "collector", None), "last_retrieval_attempt_id", "")),
                cursor_advanced=advanced,
                cursor_reason=reason,
                complete=advanced and not errors,
                partial=errors,
            )
            return result

        cls.run_scheduled_scan = run_scheduled_scan
        cls._phase2_cursor_observed = True

    wrap(Application)
    from .webhook_aware_assistant import WebhookAwarePersonalAssistant

    wrap(WebhookAwarePersonalAssistant)


def _install_stale_pending_quarantine() -> None:
    original = _configured_runtime._purge_external_pending
    if getattr(original, "_phase2_observed", False):
        return

    def purge(app: Application) -> int:
        queue = app.state.data.get("pending_delivery", [])
        collector = getattr(app, "collector", None)
        checker: Callable[[str], bool] | None = getattr(collector, "is_configured_source", None)
        if isinstance(queue, list) and callable(checker):
            for raw in list(queue):
                if not isinstance(raw, dict):
                    app.state.quarantine_pending(raw, reason="invalid_pending_row_type")
                    continue
                author = str(raw.get("author", "") or "")
                if not checker(author):
                    app.state.quarantine_pending(
                        raw,
                        reason="blocked_nonconfigured_source",
                        update_id=str(raw.get("id", "")),
                        source=author,
                    )
        return original(app)

    purge._phase2_observed = True
    _configured_runtime._purge_external_pending = purge


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _install_state_lifecycle()
    _install_filter_visibility()
    _install_retrieval_visibility()
    _install_media_prepare_visibility()
    _install_delivery_visibility()
    _install_cursor_visibility()
    _install_stale_pending_quarantine()
    _INSTALLED = True


install()
