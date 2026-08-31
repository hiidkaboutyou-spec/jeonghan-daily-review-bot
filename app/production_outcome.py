"""Structured Production Outcome Contract.

Every production run produces a deterministic, redacted, machine-readable summary
of the actual run outcome.  The watchdog consumes this structured outcome instead
of relying primarily on GitHub Actions workflow conclusion.

Design goals:
- Typed, versioned contract that is safe to persist in Actions artifacts/logs.
- Deterministic classification: HEALTHY / DEGRADED / RECOVERY_REQUIRED / FAILED.
- Privacy-safe serialization: never expose secrets, tokens, cookies, or private
  message content in the serialized artifact.
- Forward-compatible parsing: unknown future fields are tolerated; unknown
  schema versions are rejected explicitly.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 1

# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------

class OutcomeStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    RECOVERY_REQUIRED = "recovery_required"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Secret-redaction helpers
# ---------------------------------------------------------------------------
_SECRET_PATTERNS = re.compile(
    r"(?:bearer\s+|authorization|telegram[_-]?bot[_-]?token|x[_-]?cookie|"
    r"gemini[_-]?api[_-]?key|api[_-]?key|password|credential|session[_-]?token|"
    r"auth[_-]?token|cookie\s*=)",
    re.I,
)

_REDACTED_FIELDS = frozenset({
    "telegram_token",
    "x_cookies",
    "gemini_api_key",
    "TELEGRAM_BOT_TOKEN",
    "X_COOKIE",
    "GEMINI_API_KEY",
})


def _redact_value(value: Any) -> Any:
    """Return a redacted copy safe for artifact persistence."""
    if isinstance(value, str):
        if _SECRET_PATTERNS.search(value):
            return "<redacted>"
        return value
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def redact_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Produce a fully redacted copy suitable for persistence."""
    clean: dict[str, Any] = {}
    for key, value in outcome.items():
        if key in _REDACTED_FIELDS:
            continue
        clean[key] = _redact_value(value)
    return clean


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class SourceCollectionOutcome:
    configured_source_count: int = 0
    active_source_count: int = 0
    attempted_source_count: int = 0
    complete_source_count: int = 0
    partial_source_count: int = 0
    failed_source_count: int = 0
    disabled_source_count: int = 0
    failed_source_handles: list[str] = field(default_factory=list)
    failed_source_reasons: list[str] = field(default_factory=list)
    fallback_source_count: int = 0
    collection_complete: bool = False


@dataclass(slots=True)
class DiscoveryOutcome:
    discovered_record_count: int = 0
    retained_candidate_count: int = 0
    duplicate_drop_count: int = 0
    filtered_drop_count: int = 0


@dataclass(slots=True)
class AiOutcome:
    ai_jobs_attempted: int = 0
    ai_jobs_successful: int = 0
    ai_jobs_failed: int = 0
    ai_timeout_count: int = 0
    fallback_writer_count: int = 0
    translation_deferral_count: int = 0
    manual_review_count: int = 0


@dataclass(slots=True)
class TelegramOutcome:
    delivery_attempt_count: int = 0
    delivery_success_count: int = 0
    delivery_failure_count: int = 0
    media_delivery_success_count: int = 0
    media_delivery_failure_count: int = 0


@dataclass(slots=True)
class StateOutcome:
    state_checkpoint_success: bool = False
    database_checkpoint_success: bool = False
    cursor_advanced: bool = False
    cursor_reason: str = ""
    backlog_count: int = 0
    backlog_present: bool = False
    recovery_artifact_created: bool = False


@dataclass(slots=True)
class RecoveryOutcome:
    recovery_required: bool = False
    recovery_reason: str = ""
    recovery_dispatch_recommended: bool = False
    previous_recovery_context: str = ""


@dataclass(slots=True)
class ProductionOutcome:
    """Complete structured outcome of one production run."""
    # Identity
    schema_version: int = SCHEMA_VERSION
    run_id: str = ""
    run_started_at: str = ""
    run_finished_at: str = ""
    trigger_event: str = ""
    commit_sha: str = ""

    # Sub-outcomes
    source_collection: SourceCollectionOutcome = field(
        default_factory=SourceCollectionOutcome,
    )
    discovery: DiscoveryOutcome = field(default_factory=DiscoveryOutcome)
    ai: AiOutcome = field(default_factory=AiOutcome)
    telegram: TelegramOutcome = field(default_factory=TelegramOutcome)
    state: StateOutcome = field(default_factory=StateOutcome)
    recovery: RecoveryOutcome = field(default_factory=RecoveryOutcome)

    # Classification
    outcome_status: str = OutcomeStatus.HEALTHY.value
    outcome_reasons: list[str] = field(default_factory=list)
    useful_work_performed: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict with redacted secrets."""
        raw = asdict(self)
        return redact_outcome(raw)


# ---------------------------------------------------------------------------
# Builder — gathers real production counters into an outcome
# ---------------------------------------------------------------------------

class OutcomeBuilder:
    """Accumulates production counters during a run and emits the final contract."""

    def __init__(
        self,
        *,
        run_id: str = "",
        trigger_event: str = "",
        commit_sha: str = "",
    ) -> None:
        self._outcome = ProductionOutcome(
            run_id=run_id,
            trigger_event=trigger_event,
            commit_sha=commit_sha,
            run_started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._run_start = datetime.now(timezone.utc)

    @property
    def outcome(self) -> ProductionOutcome:
        return self._outcome

    # -- Identity -----------------------------------------------------------

    def finalize(self) -> ProductionOutcome:
        """Stamp finish time and classify."""
        self._outcome.run_finished_at = datetime.now(timezone.utc).isoformat()
        self._outcome.outcome_status, self._outcome.outcome_reasons = (
            classify_outcome(self._outcome)
        )
        self._outcome.useful_work_performed = _detect_useful_work(self._outcome)
        return self._outcome

    # -- Source collection --------------------------------------------------

    def set_source_totals(
        self,
        configured: int,
        active: int,
        disabled: int,
    ) -> None:
        self._outcome.source_collection.configured_source_count = configured
        self._outcome.source_collection.active_source_count = active
        self._outcome.source_collection.disabled_source_count = disabled

    def record_source_attempt(self, handle: str, *, complete: bool, error: str = "") -> None:
        sc = self._outcome.source_collection
        sc.attempted_source_count += 1
        if complete:
            sc.complete_source_count += 1
        elif error:
            sc.failed_source_count += 1
            safe_handle = normalize_source_handle(handle)
            sc.failed_source_handles.append(safe_handle)
            sc.failed_source_reasons.append(truncate_error_class(error))
        else:
            sc.partial_source_count += 1

    def set_fallback_source_count(self, count: int) -> None:
        self._outcome.source_collection.fallback_source_count = count

    def mark_collection_complete(self) -> None:
        self._outcome.source_collection.collection_complete = True

    # -- Discovery ----------------------------------------------------------

    def set_discovery(
        self,
        *,
        discovered: int,
        retained: int,
        duplicates: int,
        filtered: int = 0,
    ) -> None:
        self._outcome.discovery = DiscoveryOutcome(
            discovered_record_count=discovered,
            retained_candidate_count=retained,
            duplicate_drop_count=duplicates,
            filtered_drop_count=filtered,
        )

    # -- AI -----------------------------------------------------------------

    def record_ai_job(self, *, success: bool, timeout: bool = False, fallback: bool = False) -> None:
        ai = self._outcome.ai
        ai.ai_jobs_attempted += 1
        if success:
            ai.ai_jobs_successful += 1
        else:
            ai.ai_jobs_failed += 1
        if timeout:
            ai.ai_timeout_count += 1
        if fallback:
            ai.fallback_writer_count += 1

    def record_translation_deferral(self, count: int = 1) -> None:
        self._outcome.ai.translation_deferral_count += count

    def record_manual_review(self, count: int = 1) -> None:
        self._outcome.ai.manual_review_count += count

    # -- Telegram -----------------------------------------------------------

    def record_delivery(self, *, success: bool) -> None:
        tg = self._outcome.telegram
        tg.delivery_attempt_count += 1
        if success:
            tg.delivery_success_count += 1
        else:
            tg.delivery_failure_count += 1

    def record_media_delivery(self, *, success: bool) -> None:
        tg = self._outcome.telegram
        if success:
            tg.media_delivery_success_count += 1
        else:
            tg.media_delivery_failure_count += 1

    # -- State --------------------------------------------------------------

    def mark_state_checkpoint(self, success: bool) -> None:
        self._outcome.state.state_checkpoint_success = success

    def mark_database_checkpoint(self, success: bool) -> None:
        self._outcome.state.database_checkpoint_success = success

    def set_cursor(self, *, advanced: bool, reason: str) -> None:
        self._outcome.state.cursor_advanced = advanced
        self._outcome.state.cursor_reason = reason

    def set_backlog(self, count: int) -> None:
        self._outcome.state.backlog_count = count
        self._outcome.state.backlog_present = count > 0

    def mark_recovery_artifact(self, created: bool) -> None:
        self._outcome.state.recovery_artifact_created = created

    # -- Recovery -----------------------------------------------------------

    def set_recovery(
        self,
        *,
        required: bool,
        reason: str = "",
        dispatch_recommended: bool = False,
        previous_context: str = "",
    ) -> None:
        self._outcome.recovery = RecoveryOutcome(
            recovery_required=required,
            recovery_reason=reason,
            recovery_dispatch_recommended=dispatch_recommended,
            previous_recovery_context=previous_context,
        )


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def classify_outcome(outcome: ProductionOutcome) -> tuple[str, list[str]]:
    """Return (status, reasons) based on the structured counters.

    Classification order is strict: FAILED > RECOVERY_REQUIRED > DEGRADED > HEALTHY.
    """
    reasons: list[str] = []

    # --- FAILED checks -----------------------------------------------------
    sc = outcome.source_collection
    tg = outcome.telegram
    state = outcome.state

    # All active sources failed
    if (
        sc.active_source_count > 0
        and sc.complete_source_count == 0
        and sc.failed_source_count == sc.active_source_count
    ):
        reasons.append("all_active_sources_failed")
        return OutcomeStatus.FAILED.value, reasons

    # State/database checkpoint failure that risks correctness
    if not state.state_checkpoint_success and sc.attempted_source_count > 0:
        reasons.append("state_checkpoint_failed")
        return OutcomeStatus.FAILED.value, reasons

    # Outcome contract itself cannot be produced safely (handled by caller)

    # --- RECOVERY_REQUIRED checks ------------------------------------------
    # Collection incomplete AND cursor held
    if (
        not sc.collection_complete
        and sc.partial_source_count > 0
        and not state.cursor_advanced
    ):
        reasons.append("partial_collection_cursor_held")

    # Backlog present and growing
    if state.backlog_present and state.backlog_count > 100:
        reasons.append("large_backlog_accumulated")

    # Recovery is explicitly recommended by upstream logic
    if outcome.recovery.recovery_dispatch_recommended:
        reasons.append("recovery_dispatch_recommended")

    if reasons:
        # Only promote to RECOVERY_REQUIRED if it's not just a large backlog
        # (large backlog alone is degraded, not recovery-required)
        recovery_reasons = [
            r for r in reasons
            if r in {"partial_collection_cursor_held", "recovery_dispatch_recommended"}
        ]
        if recovery_reasons:
            return OutcomeStatus.RECOVERY_REQUIRED.value, reasons
        # Large backlog alone = DEGRADED

    reasons.clear()

    # --- DEGRADED checks ---------------------------------------------------
    # Partial sources but not all failed
    if sc.partial_source_count > 0 and sc.failed_source_count < sc.active_source_count:
        reasons.append("partial_source_collection")

    # All active sources failed but there are none (edge case: no active sources)
    # is HEALTHY, not FAILED

    # AI fallback used materially
    if outcome.ai.fallback_writer_count > 0:
        reasons.append("ai_fallback_used")

    # Translation deferred
    if outcome.ai.translation_deferral_count > 0:
        reasons.append("translation_deferred")

    # Telegram failures with preserved retry state
    if tg.delivery_failure_count > 0 and tg.delivery_failure_count < tg.delivery_attempt_count:
        reasons.append("partial_telegram_failure")

    # Total Telegram failure when delivery was attempted
    if tg.delivery_failure_count > 0 and tg.delivery_attempt_count > 0 and tg.delivery_success_count == 0:
        reasons.append("total_telegram_failure")

    # Media delivery failures
    if tg.media_delivery_failure_count > 0:
        reasons.append("media_delivery_partial_failure")

    # Large backlog
    if state.backlog_present and state.backlog_count > 100:
        reasons.append("large_backlog_accumulated")

    # Cursor not advanced (not due to partial collection, which is recovery-required)
    if not state.cursor_advanced and sc.attempted_source_count > 0 and sc.collection_complete:
        reasons.append("cursor_not_advanced")

    if reasons:
        return OutcomeStatus.DEGRADED.value, reasons

    return OutcomeStatus.HEALTHY.value, []


# ---------------------------------------------------------------------------
# Zero useful work detection
# ---------------------------------------------------------------------------

def _detect_useful_work(outcome: ProductionOutcome) -> bool:
    """Distinguish valid zero-update runs from suspicious zero-work runs.

    A fully complete collection with zero eligible posts is HEALTHY and useful
    work was performed (we successfully determined nothing new exists).
    """
    sc = outcome.source_collection

    # If collection was complete, useful work was performed even if no candidates
    if sc.collection_complete:
        return True

    # If some delivery happened, useful work was performed
    if outcome.telegram.delivery_success_count > 0:
        return True

    # If discovery found candidates but delivery was deferred
    if outcome.discovery.retained_candidate_count > 0:
        return True

    # If AI processed anything
    if outcome.ai.ai_jobs_successful > 0:
        return True

    # If collection was attempted but nothing happened
    if sc.attempted_source_count > 0 and sc.complete_source_count == 0:
        return False

    # No collection attempted at all — not a "suspicious" zero, just early return
    return True


# ---------------------------------------------------------------------------
# Privacy-safe source handle normalization
# ---------------------------------------------------------------------------

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def normalize_source_handle(handle: str) -> str:
    """Return a privacy-safe, normalized source identifier.

    Source handles are already public by nature (X/Twitter usernames) and are
    logged throughout the existing codebase. We keep them here for diagnostic
    value but truncate defensively.
    """
    value = str(handle or "").lstrip("@").strip().lower()
    if _HANDLE_RE.fullmatch(value):
        return value
    return "unknown_source"


def truncate_error_class(error: str) -> str:
    """Extract a safe technical error class identifier.

    Never persist raw exception text: it may contain auth headers, cookies,
    tokens, or private API response text.  Keep only the first identifier token.
    """
    text = str(error or "").strip()
    match = re.search(r"[A-Za-z][A-Za-z0-9_]{0,63}", text)
    if not match:
        return "Error"
    candidate = match.group(0)
    lowered = candidate.casefold()
    secret_words = {"token", "cookie", "secret", "auth", "password", "credential"}
    if any(word in lowered for word in secret_words):
        return "Error"
    return candidate


# ---------------------------------------------------------------------------
# Human-readable log summary
# ---------------------------------------------------------------------------

def format_log_summary(outcome: ProductionOutcome) -> str:
    """Produce a concise, privacy-safe human-readable summary."""
    sc = outcome.source_collection
    tg = outcome.telegram
    state = outcome.state
    ai = outcome.ai
    discovery = outcome.discovery
    recovery = outcome.recovery

    status_icon = {
        OutcomeStatus.HEALTHY.value: "🟢",
        OutcomeStatus.DEGRADED.value: "🟡",
        OutcomeStatus.RECOVERY_REQUIRED.value: "🟠",
        OutcomeStatus.FAILED.value: "🔴",
    }.get(outcome.outcome_status, "⚪")

    lines = [
        f"Production outcome: {status_icon} {outcome.outcome_status.upper()}",
        f"Sources: {sc.complete_source_count}/{sc.active_source_count} complete"
        + (f" ({sc.failed_source_count} failed)" if sc.failed_source_count else "")
        + (f" ({sc.disabled_source_count} disabled)" if sc.disabled_source_count else ""),
        f"Collection: {'complete' if sc.collection_complete else 'incomplete'}",
    ]

    if discovery.retained_candidate_count > 0 or discovery.duplicate_drop_count > 0:
        lines.append(
            f"Candidates: {discovery.retained_candidate_count}"
            f" (from {discovery.discovered_record_count} discovered,"
            f" {discovery.duplicate_drop_count} deduped)"
        )

    if ai.ai_jobs_attempted > 0:
        lines.append(
            f"AI: {ai.ai_jobs_successful}/{ai.ai_jobs_attempted} successful"
            + (f" ({ai.fallback_writer_count} fallback)" if ai.fallback_writer_count else "")
        )

    if tg.delivery_attempt_count > 0:
        lines.append(
            f"Delivered: {tg.delivery_success_count}"
            f"/{tg.delivery_attempt_count} text"
            + (
                f", {tg.media_delivery_success_count} media"
                if tg.media_delivery_success_count or tg.media_delivery_failure_count
                else ""
            )
        )

    if state.backlog_present:
        lines.append(f"Backlog: {state.backlog_count} items")

    lines.append(
        f"Cursor: {'advanced' if state.cursor_advanced else 'held'}"
        f" ({state.cursor_reason})"
    )

    if recovery.recovery_required:
        lines.append(
            f"Recovery: required — {recovery.recovery_reason}"
        )
    elif recovery.recovery_dispatch_recommended:
        lines.append("Recovery: dispatch recommended")

    if outcome.outcome_reasons:
        lines.append(f"Reasons: {', '.join(outcome.outcome_reasons)}")

    if not outcome.useful_work_performed:
        lines.append("⚠️  No useful work performed this run")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON persistence
# ---------------------------------------------------------------------------

DEFAULT_OUTCOME_PATH = Path("production-outcome.json")


def save_outcome(outcome: ProductionOutcome, path: Path | None = None) -> Path:
    """Persist the redacted outcome as JSON.  Returns the written path."""
    target = path or DEFAULT_OUTCOME_PATH
    payload = json.dumps(
        outcome.to_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    logger.info("Production outcome persisted to %s", target)
    return target


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class OutcomeValidationError(RuntimeError):
    """The outcome artifact is invalid or unreadable."""


def validate_outcome(data: dict[str, Any]) -> ProductionOutcome:
    """Validate a parsed outcome dict and return a ProductionOutcome.

    Raises OutcomeValidationError for missing/invalid required fields or
    incompatible schema versions.
    """
    if not isinstance(data, dict):
        raise OutcomeValidationError("Outcome artifact is not a JSON object")

    schema_version = data.get("schema_version")
    if schema_version is None:
        raise OutcomeValidationError("Missing required field: schema_version")
    try:
        version = int(schema_version)
    except (TypeError, ValueError):
        raise OutcomeValidationError("schema_version must be an integer")
    if version < 1 or version > SCHEMA_VERSION:
        raise OutcomeValidationError(
            f"Incompatible schema version {version}; "
            f"supported range is 1–{SCHEMA_VERSION}"
        )

    # Validate outcome_status enum
    raw_status = data.get("outcome_status", "")
    if raw_status not in {s.value for s in OutcomeStatus}:
        raise OutcomeValidationError(f"Invalid outcome_status: {raw_status!r}")

    # Build the validated outcome, tolerating unknown fields
    outcome = ProductionOutcome()
    outcome.schema_version = version
    outcome.run_id = str(data.get("run_id", ""))
    outcome.run_started_at = str(data.get("run_started_at", ""))
    outcome.run_finished_at = str(data.get("run_finished_at", ""))
    outcome.trigger_event = str(data.get("trigger_event", ""))
    outcome.commit_sha = str(data.get("commit_sha", ""))
    outcome.outcome_status = str(raw_status)
    outcome.outcome_reasons = (
        list(data["outcome_reasons"])
        if isinstance(data.get("outcome_reasons"), list)
        else []
    )
    outcome.useful_work_performed = bool(data.get("useful_work_performed", True))

    # Sub-outcomes with safe defaults
    raw_sc = data.get("source_collection")
    if isinstance(raw_sc, dict):
        outcome.source_collection = SourceCollectionOutcome(
            configured_source_count=_int_field(raw_sc, "configured_source_count"),
            active_source_count=_int_field(raw_sc, "active_source_count"),
            attempted_source_count=_int_field(raw_sc, "attempted_source_count"),
            complete_source_count=_int_field(raw_sc, "complete_source_count"),
            partial_source_count=_int_field(raw_sc, "partial_source_count"),
            failed_source_count=_int_field(raw_sc, "failed_source_count"),
            disabled_source_count=_int_field(raw_sc, "disabled_source_count"),
            failed_source_handles=_str_list_field(raw_sc, "failed_source_handles"),
            failed_source_reasons=_str_list_field(raw_sc, "failed_source_reasons"),
            fallback_source_count=_int_field(raw_sc, "fallback_source_count"),
            collection_complete=bool(raw_sc.get("collection_complete", False)),
        )

    raw_disc = data.get("discovery")
    if isinstance(raw_disc, dict):
        outcome.discovery = DiscoveryOutcome(
            discovered_record_count=_int_field(raw_disc, "discovered_record_count"),
            retained_candidate_count=_int_field(raw_disc, "retained_candidate_count"),
            duplicate_drop_count=_int_field(raw_disc, "duplicate_drop_count"),
            filtered_drop_count=_int_field(raw_disc, "filtered_drop_count"),
        )

    raw_ai = data.get("ai")
    if isinstance(raw_ai, dict):
        outcome.ai = AiOutcome(
            ai_jobs_attempted=_int_field(raw_ai, "ai_jobs_attempted"),
            ai_jobs_successful=_int_field(raw_ai, "ai_jobs_successful"),
            ai_jobs_failed=_int_field(raw_ai, "ai_jobs_failed"),
            ai_timeout_count=_int_field(raw_ai, "ai_timeout_count"),
            fallback_writer_count=_int_field(raw_ai, "fallback_writer_count"),
            translation_deferral_count=_int_field(raw_ai, "translation_deferral_count"),
            manual_review_count=_int_field(raw_ai, "manual_review_count"),
        )

    raw_tg = data.get("telegram")
    if isinstance(raw_tg, dict):
        outcome.telegram = TelegramOutcome(
            delivery_attempt_count=_int_field(raw_tg, "delivery_attempt_count"),
            delivery_success_count=_int_field(raw_tg, "delivery_success_count"),
            delivery_failure_count=_int_field(raw_tg, "delivery_failure_count"),
            media_delivery_success_count=_int_field(raw_tg, "media_delivery_success_count"),
            media_delivery_failure_count=_int_field(raw_tg, "media_delivery_failure_count"),
        )

    raw_state = data.get("state")
    if isinstance(raw_state, dict):
        outcome.state = StateOutcome(
            state_checkpoint_success=bool(raw_state.get("state_checkpoint_success")),
            database_checkpoint_success=bool(raw_state.get("database_checkpoint_success")),
            cursor_advanced=bool(raw_state.get("cursor_advanced")),
            cursor_reason=str(raw_state.get("cursor_reason", "")),
            backlog_count=_int_field(raw_state, "backlog_count"),
            backlog_present=bool(raw_state.get("backlog_present")),
            recovery_artifact_created=bool(raw_state.get("recovery_artifact_created")),
        )

    raw_recovery = data.get("recovery")
    if isinstance(raw_recovery, dict):
        outcome.recovery = RecoveryOutcome(
            recovery_required=bool(raw_recovery.get("recovery_required")),
            recovery_reason=str(raw_recovery.get("recovery_reason", "")),
            recovery_dispatch_recommended=bool(raw_recovery.get("recovery_dispatch_recommended")),
            previous_recovery_context=str(raw_recovery.get("previous_recovery_context", "")),
        )

    return outcome


def load_outcome(path: Path | None = None) -> ProductionOutcome:
    """Load and validate an outcome artifact from disk."""
    target = path or DEFAULT_OUTCOME_PATH
    if not target.exists():
        raise OutcomeValidationError(f"Outcome artifact not found: {target}")
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OutcomeValidationError(f"Could not read outcome artifact: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutcomeValidationError(f"Outcome artifact is malformed JSON: {exc}") from exc
    return validate_outcome(data)


# ---------------------------------------------------------------------------
# Safe parsing helpers
# ---------------------------------------------------------------------------

def _int_field(data: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(data.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _str_list_field(data: dict[str, Any], key: str) -> list[str]:
    raw = data.get(key)
    if isinstance(raw, list):
        return [str(item)[:80] for item in raw if item is not None]
    return []
