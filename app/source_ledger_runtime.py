from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from .raw_observation import RawObservationStore
from .source_ledger import SourceLedgerStore, SourceWindowResult, SourceWindowStatus
from .x_client import _safe_error, normalize_handle
from .x_completeness import CompleteWindowXCollector, XCompletenessError

_INSTALLED = False


def _db_path(collector: Any) -> Path:
    existing = getattr(collector, "raw_observation_store", None)
    if isinstance(existing, RawObservationStore):
        return existing.path
    override = str(os.environ.get("RAW_OBSERVATION_DB_PATH", "") or "").strip()
    if override:
        return Path(override)
    x_db = Path(getattr(collector, "db_path", Path(".state/x_accounts.db")))
    return x_db.with_name("private-review.sqlite3")


def _ledger_for(collector: Any) -> SourceLedgerStore:
    existing = getattr(collector, "source_ledger_store", None)
    if isinstance(existing, SourceLedgerStore):
        return existing
    store = SourceLedgerStore(_db_path(collector))
    collector.source_ledger_store = store
    return store


def _new_ledger_attempt_id() -> str:
    """Create a ledger-owned attempt ID independent of legacy observability layers."""
    return uuid.uuid4().hex[:20]


def _raw_count(collector: Any, handle: str, start_iso: str, end_iso: str) -> int:
    store = getattr(collector, "raw_observation_store", None)
    temporary_store = False
    if not isinstance(store, RawObservationStore):
        try:
            store = RawObservationStore(_db_path(collector))
            temporary_store = True
        except Exception:
            return 0
    try:
        row = store.conn.execute(
            """
            SELECT count(*) FROM raw_observations
            WHERE source_handle=? AND created_at>=? AND created_at<?
            """,
            (normalize_handle(handle).casefold(), start_iso, end_iso),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        if temporary_store:
            store.close()


def install() -> None:
    global _INSTALLED
    if _INSTALLED or CompleteWindowXCollector.__dict__.get("_source_ledger_installed", False):
        _INSTALLED = True
        return

    original = CompleteWindowXCollector._collect_source_timeline

    async def wrapped(self, handle, start, end, *, limit: int, include_replies: bool):
        source = normalize_handle(handle)
        start_iso = start.isoformat() if hasattr(start, "isoformat") else str(start)
        end_iso = end.isoformat() if hasattr(end, "isoformat") else str(end)
        attempt_id = _new_ledger_attempt_id()
        ledger = _ledger_for(self)
        ledger.start_attempt(
            source_handle=source,
            window_start=start_iso,
            window_end=end_iso,
            attempt_id=attempt_id,
        )
        try:
            updates = await original(
                self,
                handle,
                start,
                end,
                limit=limit,
                include_replies=include_replies,
            )
        except XCompletenessError as exc:
            ledger.finish(SourceWindowResult(
                source_handle=source,
                window_start=start_iso,
                window_end=end_iso,
                status=SourceWindowStatus.PARTIAL,
                attempt_id=attempt_id,
                raw_observation_count=_raw_count(self, source, start_iso, end_iso),
                error_class=type(exc).__name__,
                error_summary=_safe_error(exc),
                proof_kind="bounded_timeline_limit_or_incomplete",
            ))
            raise
        except Exception as exc:
            ledger.finish(SourceWindowResult(
                source_handle=source,
                window_start=start_iso,
                window_end=end_iso,
                status=SourceWindowStatus.UNPROVEN,
                attempt_id=attempt_id,
                raw_observation_count=_raw_count(self, source, start_iso, end_iso),
                error_class=type(exc).__name__,
                error_summary=_safe_error(exc),
                proof_kind="provider_failure",
            ))
            raise

        ledger.finish(SourceWindowResult(
            source_handle=source,
            window_start=start_iso,
            window_end=end_iso,
            status=SourceWindowStatus.COMPLETE,
            attempt_id=attempt_id,
            raw_observation_count=_raw_count(self, source, start_iso, end_iso),
            retained_count=len(updates),
            proof_kind="timeline_exhausted_or_lower_boundary_crossed",
        ))
        return updates

    CompleteWindowXCollector._collect_source_timeline = wrapped
    CompleteWindowXCollector._source_ledger_installed = True
    _INSTALLED = True


install()
