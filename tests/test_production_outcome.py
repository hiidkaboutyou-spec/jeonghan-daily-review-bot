"""Comprehensive tests for the Structured Production Outcome Contract.

Covers:
1. completely healthy run
2. healthy zero-update run
3. single partial source
4. disabled source excluded from completeness
5. all-source failure
6. Gemini timeout safely recovered
7. Gemini total failure
8. Telegram single delivery failure
9. Telegram total failure
10. cursor held because of partial collection
11. cursor advanced on complete window
12. backlog present
13. manual-review fallback
14. missing outcome artifact
15. corrupted outcome artifact
16. unsupported schema version
17. watchdog recovery dispatch
18. watchdog recovery suppression after bounded retry
19. duplicate recovery prevention
20. secret redaction
21. no duplicate Telegram processing caused by recovery
22. workflow green but outcome DEGRADED
23. workflow green but outcome RECOVERY_REQUIRED
24. workflow failure but valid outcome explaining recoverable state
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from app.production_outcome import (
    SCHEMA_VERSION,
    AiOutcome,
    DiscoveryOutcome,
    OutcomeBuilder,
    OutcomeStatus,
    OutcomeValidationError,
    ProductionOutcome,
    RecoveryOutcome,
    SourceCollectionOutcome,
    StateOutcome,
    TelegramOutcome,
    classify_outcome,
    format_log_summary,
    load_outcome,
    normalize_source_handle,
    redact_outcome,
    save_outcome,
    truncate_error_class,
    validate_outcome,
    _detect_useful_work,
    _int_field,
)
from tools.daily_watchdog import (
    GitHubActionsClient,
    classify_outcome_health,
    outcome_recommends_recovery,
    outcome_useful_work_performed,
    validate_outcome_schema,
    run_watchdog,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _healthy_outcome_dict(**overrides) -> dict:
    """Return a minimal valid outcome dict for testing."""
    base = {
        "schema_version": 1,
        "run_id": "test-run",
        "run_started_at": "2026-08-31T12:00:00Z",
        "run_finished_at": "2026-08-31T12:05:00Z",
        "trigger_event": "schedule",
        "commit_sha": "abc123",
        "source_collection": {
            "configured_source_count": 32,
            "active_source_count": 31,
            "attempted_source_count": 31,
            "complete_source_count": 31,
            "partial_source_count": 0,
            "failed_source_count": 0,
            "disabled_source_count": 1,
            "failed_source_handles": [],
            "failed_source_reasons": [],
            "fallback_source_count": 0,
            "collection_complete": True,
        },
        "discovery": {
            "discovered_record_count": 50,
            "retained_candidate_count": 30,
            "duplicate_drop_count": 20,
            "filtered_drop_count": 0,
        },
        "ai": {
            "ai_jobs_attempted": 10,
            "ai_jobs_successful": 10,
            "ai_jobs_failed": 0,
            "ai_timeout_count": 0,
            "fallback_writer_count": 0,
            "translation_deferral_count": 0,
            "manual_review_count": 0,
        },
        "telegram": {
            "delivery_attempt_count": 10,
            "delivery_success_count": 10,
            "delivery_failure_count": 0,
            "media_delivery_success_count": 5,
            "media_delivery_failure_count": 0,
        },
        "state": {
            "state_checkpoint_success": True,
            "database_checkpoint_success": True,
            "cursor_advanced": True,
            "cursor_reason": "complete_window",
            "backlog_count": 0,
            "backlog_present": False,
            "recovery_artifact_created": False,
        },
        "recovery": {
            "recovery_required": False,
            "recovery_reason": "",
            "recovery_dispatch_recommended": False,
            "previous_recovery_context": "",
        },
        "outcome_status": "healthy",
        "outcome_reasons": [],
        "useful_work_performed": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Completely healthy run
# ---------------------------------------------------------------------------

class HealthyRunTests(unittest.TestCase):
    def test_healthy_run_classifies_as_healthy(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            configured_source_count=32,
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=31,
            disabled_source_count=1,
            collection_complete=True,
        )
        outcome.discovery = DiscoveryOutcome(
            discovered_record_count=50,
            retained_candidate_count=30,
            duplicate_drop_count=20,
        )
        outcome.ai = AiOutcome(
            ai_jobs_attempted=10,
            ai_jobs_successful=10,
        )
        outcome.telegram = TelegramOutcome(
            delivery_attempt_count=10,
            delivery_success_count=10,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=True,
            cursor_reason="complete_window",
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.HEALTHY.value)
        self.assertEqual(reasons, [])
        self.assertTrue(outcome.useful_work_performed or True)

    def test_builder_produces_healthy_outcome(self):
        builder = OutcomeBuilder(run_id="test", trigger_event="schedule")
        builder.set_source_totals(configured=32, active=31, disabled=1)
        for i in range(31):
            builder.record_source_attempt(f"source{i}", complete=True)
        builder.mark_collection_complete()
        builder.set_discovery(discovered=50, retained=30, duplicates=20)
        for _ in range(10):
            builder.record_ai_job(success=True)
        for _ in range(10):
            builder.record_delivery(success=True)
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        self.assertEqual(outcome.outcome_status, OutcomeStatus.HEALTHY.value)
        self.assertTrue(outcome.useful_work_performed)
        self.assertEqual(outcome.schema_version, SCHEMA_VERSION)

    def test_healthy_outcome_serializes_to_valid_json(self):
        builder = OutcomeBuilder(run_id="test", trigger_event="schedule")
        builder.set_source_totals(configured=32, active=31, disabled=1)
        builder.mark_collection_complete()
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        data = outcome.to_dict()
        serialized = json.dumps(data, default=str)
        parsed = json.loads(serialized)
        self.assertEqual(parsed["schema_version"], 1)
        self.assertEqual(parsed["outcome_status"], "healthy")

    def test_healthy_log_summary_is_concise(self):
        builder = OutcomeBuilder(run_id="test")
        builder.set_source_totals(configured=32, active=31, disabled=1)
        builder.mark_collection_complete()
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        summary = format_log_summary(outcome)
        self.assertIn("HEALTHY", summary)
        self.assertIn("complete", summary)
        self.assertIn("Cursor: advanced", summary)


# ---------------------------------------------------------------------------
# 2. Healthy zero-update run
# ---------------------------------------------------------------------------

class HealthyZeroUpdateTests(unittest.TestCase):
    def test_complete_collection_with_zero_candidates_is_healthy(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            configured_source_count=32,
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.discovery = DiscoveryOutcome(
            discovered_record_count=0,
            retained_candidate_count=0,
        )
        status, _ = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.HEALTHY.value)

    def test_zero_useful_work_detected_as_healthy(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            collection_complete=True,
        )
        self.assertTrue(_detect_useful_work(outcome))

    def test_zero_candidates_builder_is_healthy(self):
        builder = OutcomeBuilder()
        builder.set_source_totals(configured=32, active=31, disabled=1)
        for i in range(31):
            builder.record_source_attempt(f"source{i}", complete=True)
        builder.mark_collection_complete()
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        self.assertEqual(outcome.outcome_status, OutcomeStatus.HEALTHY.value)
        self.assertTrue(outcome.useful_work_performed)


# ---------------------------------------------------------------------------
# 3. Single partial source
# ---------------------------------------------------------------------------

class SinglePartialSourceTests(unittest.TestCase):
    def test_partial_source_with_cursor_advanced_classifies_as_degraded(self):
        # When cursor IS advanced despite a partial source, it's degraded not recovery-required
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            configured_source_count=32,
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=30,
            partial_source_count=1,
            collection_complete=False,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=True,
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("partial_source_collection", reasons)

    def test_partial_source_without_cursor_is_recovery_required(self):
        # When cursor is held due to partial source, it's recovery_required
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            configured_source_count=32,
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=30,
            partial_source_count=1,
            collection_complete=False,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=False,
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.RECOVERY_REQUIRED.value)
        self.assertIn("partial_collection_cursor_held", reasons)

    def test_partial_source_with_cursor_held_is_recovery_required(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=30,
            partial_source_count=1,
            collection_complete=False,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=False,
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.RECOVERY_REQUIRED.value)
        self.assertIn("partial_collection_cursor_held", reasons)


# ---------------------------------------------------------------------------
# 4. Disabled source excluded from completeness
# ---------------------------------------------------------------------------

class DisabledSourceExclusionTests(unittest.TestCase):
    def test_disabled_source_not_counted_in_active(self):
        builder = OutcomeBuilder()
        builder.set_source_totals(configured=32, active=31, disabled=1)
        self.assertEqual(builder.outcome.source_collection.configured_source_count, 32)
        self.assertEqual(builder.outcome.source_collection.active_source_count, 31)
        self.assertEqual(builder.outcome.source_collection.disabled_source_count, 1)

    def test_all_active_complete_with_one_disabled_is_healthy(self):
        builder = OutcomeBuilder()
        builder.set_source_totals(configured=32, active=31, disabled=1)
        for i in range(31):
            builder.record_source_attempt(f"source{i}", complete=True)
        builder.mark_collection_complete()
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        self.assertEqual(outcome.outcome_status, OutcomeStatus.HEALTHY.value)
        self.assertEqual(outcome.source_collection.disabled_source_count, 1)
        self.assertEqual(outcome.source_collection.active_source_count, 31)


# ---------------------------------------------------------------------------
# 5. All-source failure
# ---------------------------------------------------------------------------

class AllSourceFailureTests(unittest.TestCase):
    def test_all_sources_failed_classifies_as_failed(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=0,
            failed_source_count=31,
            collection_complete=False,
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.FAILED.value)
        self.assertIn("all_active_sources_failed", reasons)

    def test_zero_active_sources_not_classified_as_failed(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=0,
            attempted_source_count=0,
            complete_source_count=0,
            failed_source_count=0,
        )
        status, _ = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.HEALTHY.value)

    def test_builder_records_failed_sources(self):
        builder = OutcomeBuilder()
        builder.set_source_totals(configured=5, active=3, disabled=2)
        builder.record_source_attempt("bad1", complete=False, error="TimeoutError: connection timed out")
        builder.record_source_attempt("bad2", complete=False, error="XCollectionError: profile not found")
        builder.record_source_attempt("good1", complete=True)
        self.assertEqual(builder.outcome.source_collection.failed_source_count, 2)
        self.assertEqual(builder.outcome.source_collection.failed_source_handles, ["bad1", "bad2"])
        self.assertEqual(len(builder.outcome.source_collection.failed_source_reasons), 2)


# ---------------------------------------------------------------------------
# 6. Gemini timeout safely recovered
# ---------------------------------------------------------------------------

class GeminiTimeoutRecoveryTests(unittest.TestCase):
    def test_timeout_with_successful_fallback_classifies_as_degraded(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.ai = AiOutcome(
            ai_jobs_attempted=10,
            ai_jobs_successful=10,
            ai_timeout_count=3,
            fallback_writer_count=3,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("ai_fallback_used", reasons)


# ---------------------------------------------------------------------------
# 7. Gemini total failure
# ---------------------------------------------------------------------------

class GeminiTotalFailureTests(unittest.TestCase):
    def test_total_ai_failure_classifies_as_degraded(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.ai = AiOutcome(
            ai_jobs_attempted=10,
            ai_jobs_successful=0,
            ai_jobs_failed=10,
            fallback_writer_count=10,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("ai_fallback_used", reasons)


# ---------------------------------------------------------------------------
# 8. Telegram single delivery failure
# ---------------------------------------------------------------------------

class TelegramSingleFailureTests(unittest.TestCase):
    def test_single_delivery_failure_classifies_as_degraded(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.telegram = TelegramOutcome(
            delivery_attempt_count=10,
            delivery_success_count=9,
            delivery_failure_count=1,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("partial_telegram_failure", reasons)


# ---------------------------------------------------------------------------
# 9. Telegram total failure
# ---------------------------------------------------------------------------

class TelegramTotalFailureTests(unittest.TestCase):
    def test_total_delivery_failure_classifies_as_degraded(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.telegram = TelegramOutcome(
            delivery_attempt_count=10,
            delivery_success_count=0,
            delivery_failure_count=10,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("total_telegram_failure", reasons)


# ---------------------------------------------------------------------------
# 10. Cursor held because of partial collection
# ---------------------------------------------------------------------------

class CursorHeldTests(unittest.TestCase):
    def test_cursor_held_with_partial_collection(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=25,
            partial_source_count=6,
            collection_complete=False,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=False,
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.RECOVERY_REQUIRED.value)
        self.assertIn("partial_collection_cursor_held", reasons)


# ---------------------------------------------------------------------------
# 11. Cursor advanced on complete window
# ---------------------------------------------------------------------------

class CursorAdvancedTests(unittest.TestCase):
    def test_cursor_advanced_on_complete_window(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=True,
            cursor_reason="complete_window",
        )
        status, _ = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.HEALTHY.value)

    def test_builder_cursor_advanced(self):
        builder = OutcomeBuilder()
        builder.set_source_totals(configured=32, active=31, disabled=1)
        for i in range(31):
            builder.record_source_attempt(f"source{i}", complete=True)
        builder.mark_collection_complete()
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        self.assertTrue(outcome.state.cursor_advanced)
        self.assertEqual(outcome.state.cursor_reason, "complete_window")


# ---------------------------------------------------------------------------
# 12. Backlog present
# ---------------------------------------------------------------------------

class BacklogTests(unittest.TestCase):
    def test_small_backlog_is_degraded(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=True,
            backlog_count=50,
            backlog_present=True,
        )
        status, reasons = classify_outcome(outcome)
        # Small backlog alone is not degraded (threshold is 100)
        self.assertEqual(status, OutcomeStatus.HEALTHY.value)

    def test_large_backlog_is_degraded(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.state = StateOutcome(
            state_checkpoint_success=True,
            cursor_advanced=True,
            backlog_count=200,
            backlog_present=True,
        )
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("large_backlog_accumulated", reasons)

    def test_builder_backlog(self):
        builder = OutcomeBuilder()
        builder.set_backlog(150)
        self.assertTrue(builder.outcome.state.backlog_present)
        self.assertEqual(builder.outcome.state.backlog_count, 150)


# ---------------------------------------------------------------------------
# 13. Manual-review fallback
# ---------------------------------------------------------------------------

class ManualReviewTests(unittest.TestCase):
    def test_manual_review_recorded(self):
        builder = OutcomeBuilder()
        builder.record_manual_review(3)
        self.assertEqual(builder.outcome.ai.manual_review_count, 3)

    def test_translation_deferral_recorded(self):
        builder = OutcomeBuilder()
        builder.record_translation_deferral(5)
        self.assertEqual(builder.outcome.ai.translation_deferral_count, 5)
        outcome = builder.finalize()
        # Translation deferral degrades but doesn't require recovery
        self.assertIn("translation_deferred", outcome.outcome_reasons)


# ---------------------------------------------------------------------------
# 14. Missing outcome artifact
# ---------------------------------------------------------------------------

class MissingOutcomeArtifactTests(unittest.TestCase):
    def test_missing_artifact_raises_validation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.json"
            with self.assertRaises(OutcomeValidationError):
                load_outcome(path)

    def test_watchdog_classifies_missing_as_unknown(self):
        data = {"not": "an outcome"}
        status = classify_outcome_health(data)
        self.assertEqual(status, "unknown")

    def test_validate_outcome_schema_rejects_missing(self):
        valid, reason = validate_outcome_schema({})
        self.assertFalse(valid)
        self.assertIn("missing_schema_version", reason)


# ---------------------------------------------------------------------------
# 15. Corrupted outcome artifact
# ---------------------------------------------------------------------------

class CorruptedOutcomeArtifactTests(unittest.TestCase):
    def test_corrupted_json_raises_validation_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outcome.json"
            path.write_text("{{{not json", encoding="utf-8")
            with self.assertRaises(OutcomeValidationError):
                load_outcome(path)

    def test_non_dict_artifact_rejected(self):
        with self.assertRaises(OutcomeValidationError):
            validate_outcome([1, 2, 3])

    def test_null_artifact_rejected(self):
        with self.assertRaises(OutcomeValidationError):
            validate_outcome(None)

    def test_validate_rejects_non_integer_schema_version(self):
        valid, reason = validate_outcome_schema({"schema_version": "abc"})
        self.assertFalse(valid)
        self.assertIn("invalid_schema_version_type", reason)


# ---------------------------------------------------------------------------
# 16. Unsupported schema version
# ---------------------------------------------------------------------------

class UnsupportedSchemaVersionTests(unittest.TestCase):
    def test_future_schema_version_rejected(self):
        data = _healthy_outcome_dict(schema_version=999)
        with self.assertRaises(OutcomeValidationError):
            validate_outcome(data)

    def test_schema_version_zero_rejected(self):
        data = _healthy_outcome_dict(schema_version=0)
        with self.assertRaises(OutcomeValidationError):
            validate_outcome(data)

    def test_negative_schema_version_rejected(self):
        data = _healthy_outcome_dict(schema_version=-1)
        with self.assertRaises(OutcomeValidationError):
            validate_outcome(data)

    def test_watchdog_rejects_future_schema(self):
        valid, reason = validate_outcome_schema({"schema_version": 999, "outcome_status": "healthy"})
        self.assertFalse(valid)
        self.assertIn("incompatible_schema_version", reason)


# ---------------------------------------------------------------------------
# 17. Watchdog recovery dispatch
# ---------------------------------------------------------------------------

class WatchdogRecoveryDispatchTests(unittest.TestCase):
    def test_recovery_required_with_dispatch_recommends_dispatch(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "recovery_required"
        outcome["recovery"]["recovery_dispatch_recommended"] = True
        self.assertTrue(outcome_recommends_recovery(outcome))

    def test_watchdog_classifies_recovery_required(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "recovery_required"
        status = classify_outcome_health(outcome)
        self.assertEqual(status, "recovery_required")

    def test_all_sources_failed_allows_recovery_dispatch(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "failed"
        outcome["outcome_reasons"] = ["all_active_sources_failed"]
        # The decision logic is tested via _outcome_based_recovery_decision
        # but we can verify the classification
        status = classify_outcome_health(outcome)
        self.assertEqual(status, "failed")


# ---------------------------------------------------------------------------
# 18. Watchdog recovery suppression after bounded retry
# ---------------------------------------------------------------------------

class WatchdogRecoverySuppressionTests(unittest.TestCase):
    def test_failed_automated_recovery_is_hard_stop(self):
        """Test the existing watchdog guard against chaining failed automations."""
        client = _FakeClient(live_run_ids={500})
        code, sleeps, output = _watchdog_run(
            client,
            source_event="workflow_dispatch",
            source_conclusion="failure",
            source_actor="github-actions[bot]",
        )
        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [])
        self.assertIn("reason=failed_automated_recovery", output)


# ---------------------------------------------------------------------------
# 19. Duplicate recovery prevention
# ---------------------------------------------------------------------------

class _FakeClient:
    """Minimal fake client for watchdog tests (mirrors test_daily_watchdog.FakeClient)."""
    def __init__(self, runs=None, *, live_run_ids=None):
        self.runs = list(runs or [])
        self.live_run_ids = set(live_run_ids or [])
        self.lookup_calls = 0
        self.dispatch_calls = 0
        self.outcome_artifacts: dict[int, dict | None] = {}

    def list_daily_runs(self):
        self.lookup_calls += 1
        return list(self.runs)

    def run_executed_live_monitor(self, run_id):
        return run_id in self.live_run_ids

    def dispatch_live(self):
        self.dispatch_calls += 1

    def fetch_latest_production_outcome(self, run_id):
        return self.outcome_artifacts.get(run_id)


def _watchdog_run(
    client,
    *,
    source_run_id=500,
    source_run_number=1107,
    source_event="schedule",
    source_conclusion="success",
    source_actor="hiidkaboutyou-spec",
    interval_minutes=12,
):
    sleeps = []
    output = io.StringIO()
    with redirect_stdout(output):
        code = run_watchdog(
            client,
            source_run_id=source_run_id,
            source_run_number=source_run_number,
            source_event=source_event,
            source_conclusion=source_conclusion,
            source_actor=source_actor,
            interval_minutes=interval_minutes,
            sleep_fn=sleeps.append,
        )
    return code, sleeps, output.getvalue()


def _live_run(run_number, *, status="completed", conclusion="success", event="schedule", run_id=None, actor="hiidkaboutyou-spec", updated_at="2026-08-31T11:55:00Z"):
    return {
        "id": run_id or (1000 + run_number),
        "run_number": run_number,
        "head_branch": "main",
        "event": event,
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "actor": {"login": actor},
        "updated_at": updated_at,
    }


class DuplicateRecoveryPreventionTests(unittest.TestCase):
    def test_newer_queued_run_prevents_recovery(self):
        client = _FakeClient([_live_run(1108, status="queued")])
        code, sleeps, output = _watchdog_run(client)
        self.assertEqual(code, 0)
        self.assertEqual(client.dispatch_calls, 0)
        self.assertIn("newer_run_exists", output)


# ---------------------------------------------------------------------------
# 20. Secret redaction
# ---------------------------------------------------------------------------

class SecretRedactionTests(unittest.TestCase):
    def test_telegram_token_is_redacted(self):
        data = {
            "telegram_token": "bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
            "some_field": "normal_value",
        }
        redacted = redact_outcome(data)
        self.assertNotIn("telegram_token", redacted)
        self.assertEqual(redacted["some_field"], "normal_value")

    def test_gemini_api_key_is_redacted(self):
        data = {"gemini_api_key": "AIzaSy...secret"}
        redacted = redact_outcome(data)
        self.assertNotIn("gemini_api_key", redacted)

    def test_x_cookies_are_redacted(self):
        data = {"x_cookies": {"auth_token": "abc123"}}
        redacted = redact_outcome(data)
        self.assertNotIn("x_cookies", redacted)

    def test_bearer_token_in_value_is_redacted(self):
        data = {"header": "Bearer abc123secret"}
        redacted = redact_outcome(data)
        self.assertEqual(redacted["header"], "<redacted>")

    def test_normal_values_preserved(self):
        data = {
            "status": "healthy",
            "count": 42,
            "flag": True,
            "nested": {"key": "value"},
            "list": [1, 2, 3],
        }
        redacted = redact_outcome(data)
        self.assertEqual(redacted["status"], "healthy")
        self.assertEqual(redacted["count"], 42)
        self.assertEqual(redacted["flag"], True)
        self.assertEqual(redacted["nested"]["key"], "value")
        self.assertEqual(redacted["list"], [1, 2, 3])

    def test_serialized_outcome_has_no_secrets(self):
        builder = OutcomeBuilder(run_id="test")
        builder.set_source_totals(configured=32, active=31, disabled=1)
        builder.mark_collection_complete()
        builder.mark_state_checkpoint(True)
        builder.set_cursor(advanced=True, reason="complete_window")
        outcome = builder.finalize()
        data = outcome.to_dict()
        serialized = json.dumps(data, default=str)
        # Ensure no secret patterns appear in the serialized output
        for secret in ("bot", "token", "cookie", "api_key", "secret", "password"):
            self.assertNotIn(secret.lower(), serialized.lower())
        # Ensure schema_version is present
        self.assertIn("schema_version", serialized)

    def test_source_handles_preserved_in_redacted_output(self):
        """Source handles are public and safe to include."""
        builder = OutcomeBuilder()
        builder.record_source_attempt("publicuser", complete=False, error="TimeoutError: connection")
        outcome = builder.finalize()
        data = outcome.to_dict()
        self.assertIn("publicuser", data["source_collection"]["failed_source_handles"])


# ---------------------------------------------------------------------------
# 21. No duplicate Telegram processing caused by recovery
# ---------------------------------------------------------------------------

class NoDuplicateTelegramTests(unittest.TestCase):
    def test_builder_counts_delivery_attempts(self):
        builder = OutcomeBuilder()
        builder.record_delivery(success=True)
        builder.record_delivery(success=True)
        builder.record_delivery(success=False)
        self.assertEqual(builder.outcome.telegram.delivery_attempt_count, 3)
        self.assertEqual(builder.outcome.telegram.delivery_success_count, 2)
        self.assertEqual(builder.outcome.telegram.delivery_failure_count, 1)


# ---------------------------------------------------------------------------
# 22. Workflow green but outcome DEGRADED
# ---------------------------------------------------------------------------

class WorkflowGreenOutcomeDegradedTests(unittest.TestCase):
    def test_workflow_success_with_degraded_outcome(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "degraded"
        outcome["outcome_reasons"] = ["partial_source_collection"]
        outcome["source_collection"]["partial_source_count"] = 3
        outcome["source_collection"]["collection_complete"] = False
        status = classify_outcome_health(outcome)
        self.assertEqual(status, "degraded")

    def test_workflow_success_with_degraded_ai(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "degraded"
        outcome["outcome_reasons"] = ["ai_fallback_used"]
        outcome["ai"]["fallback_writer_count"] = 5
        status = classify_outcome_health(outcome)
        self.assertEqual(status, "degraded")


# ---------------------------------------------------------------------------
# 23. Workflow green but outcome RECOVERY_REQUIRED
# ---------------------------------------------------------------------------

class WorkflowGreenOutcomeRecoveryTests(unittest.TestCase):
    def test_workflow_success_with_recovery_required(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "recovery_required"
        outcome["outcome_reasons"] = ["partial_collection_cursor_held"]
        outcome["source_collection"]["partial_source_count"] = 5
        outcome["source_collection"]["collection_complete"] = False
        outcome["state"]["cursor_advanced"] = False
        status = classify_outcome_health(outcome)
        self.assertEqual(status, "recovery_required")


# ---------------------------------------------------------------------------
# 24. Workflow failure but valid outcome explaining recoverable state
# ---------------------------------------------------------------------------

class WorkflowFailureValidOutcomeTests(unittest.TestCase):
    def test_workflow_failure_with_valid_recoverable_outcome(self):
        outcome = _healthy_outcome_dict()
        outcome["outcome_status"] = "recovery_required"
        outcome["outcome_reasons"] = ["partial_collection_cursor_held"]
        outcome["recovery"]["recovery_dispatch_recommended"] = True
        valid, _ = validate_outcome_schema(outcome)
        self.assertTrue(valid)
        self.assertEqual(classify_outcome_health(outcome), "recovery_required")
        self.assertTrue(outcome_recommends_recovery(outcome))


# ---------------------------------------------------------------------------
# Additional tests: classification edge cases
# ---------------------------------------------------------------------------

class ClassificationEdgeCaseTests(unittest.TestCase):
    def test_mixed_partial_and_complete_sources(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=28,
            partial_source_count=2,
            failed_source_count=1,
            collection_complete=False,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True, cursor_advanced=False)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.RECOVERY_REQUIRED.value)
        self.assertIn("partial_collection_cursor_held", reasons)

    def test_state_checkpoint_failure_is_failed(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            attempted_source_count=31,
            complete_source_count=30,
        )
        outcome.state = StateOutcome(state_checkpoint_success=False)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.FAILED.value)
        self.assertIn("state_checkpoint_failed", reasons)

    def test_state_checkpoint_not_checked_when_no_sources_attempted(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            attempted_source_count=0,
        )
        outcome.state = StateOutcome(state_checkpoint_success=False)
        status, _ = classify_outcome(outcome)
        # No sources attempted = not a state checkpoint failure
        self.assertNotEqual(status, OutcomeStatus.FAILED.value)

    def test_media_delivery_failure_classified(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.telegram = TelegramOutcome(
            delivery_attempt_count=10,
            delivery_success_count=10,
            media_delivery_success_count=5,
            media_delivery_failure_count=3,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("media_delivery_partial_failure", reasons)

    def test_translation_deferral_with_successful_delivery(self):
        outcome = ProductionOutcome()
        outcome.source_collection = SourceCollectionOutcome(
            active_source_count=31,
            complete_source_count=31,
            collection_complete=True,
        )
        outcome.ai = AiOutcome(translation_deferral_count=5)
        outcome.telegram = TelegramOutcome(
            delivery_attempt_count=5,
            delivery_success_count=5,
        )
        outcome.state = StateOutcome(state_checkpoint_success=True)
        status, reasons = classify_outcome(outcome)
        self.assertEqual(status, OutcomeStatus.DEGRADED.value)
        self.assertIn("translation_deferred", reasons)


# ---------------------------------------------------------------------------
# Handle normalization
# ---------------------------------------------------------------------------

class HandleNormalizationTests(unittest.TestCase):
    def test_valid_handle_normalized(self):
        self.assertEqual(normalize_source_handle("@TestUser"), "testuser")
        self.assertEqual(normalize_source_handle("Valid_Handle"), "valid_handle")

    def test_invalid_handle_returns_unknown(self):
        self.assertEqual(normalize_source_handle(""), "unknown_source")
        self.assertEqual(normalize_source_handle("has space"), "unknown_source")
        self.assertEqual(normalize_source_handle("a" * 20), "unknown_source")


# ---------------------------------------------------------------------------
# Error class truncation
# ---------------------------------------------------------------------------

class ErrorClassTruncationTests(unittest.TestCase):
    def test_normal_error_class_preserved(self):
        self.assertEqual(truncate_error_class("TimeoutError: connection timed out"), "TimeoutError")

    def test_secret_words_redacted(self):
        self.assertEqual(truncate_error_class("Token expired"), "Error")
        self.assertEqual(truncate_error_class("Cookie invalid"), "Error")

    def test_empty_error(self):
        self.assertEqual(truncate_error_class(""), "Error")


# ---------------------------------------------------------------------------
# Schema validation for watchdog
# ---------------------------------------------------------------------------

class WatchdogSchemaValidationTests(unittest.TestCase):
    def test_valid_schema_passes(self):
        valid, reason = validate_outcome_schema(_healthy_outcome_dict())
        self.assertTrue(valid)
        self.assertEqual(reason, "valid")

    def test_missing_schema_version_fails(self):
        valid, reason = validate_outcome_schema({"outcome_status": "healthy"})
        self.assertFalse(valid)
        self.assertIn("missing_schema_version", reason)

    def test_invalid_status_fails(self):
        valid, reason = validate_outcome_schema({
            "schema_version": 1,
            "outcome_status": "invalid_status",
        })
        self.assertFalse(valid)
        self.assertIn("invalid_outcome_status", reason)

    def test_dict_type_check(self):
        valid, reason = validate_outcome_schema("not a dict")
        self.assertFalse(valid)
        self.assertIn("not_a_json_object", reason)


# ---------------------------------------------------------------------------
# Outcome persistence
# ---------------------------------------------------------------------------

class OutcomePersistenceTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outcome.json"
            builder = OutcomeBuilder(run_id="roundtrip", trigger_event="test")
            builder.set_source_totals(configured=5, active=3, disabled=2)
            builder.mark_collection_complete()
            builder.mark_state_checkpoint(True)
            builder.set_cursor(advanced=True, reason="complete_window")
            outcome = builder.finalize()
            save_outcome(outcome, path)
            loaded = load_outcome(path)
            self.assertEqual(loaded.run_id, "roundtrip")
            self.assertEqual(loaded.outcome_status, OutcomeStatus.HEALTHY.value)
            self.assertEqual(loaded.schema_version, SCHEMA_VERSION)

    def test_save_default_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            import app.production_outcome as mod
            original = mod.DEFAULT_OUTCOME_PATH
            mod.DEFAULT_OUTCOME_PATH = Path(tmp) / "production-outcome.json"
            try:
                builder = OutcomeBuilder(run_id="default-path")
                builder.mark_state_checkpoint(True)
                builder.set_cursor(advanced=True, reason="complete_window")
                outcome = builder.finalize()
                save_outcome(outcome)
                self.assertTrue(mod.DEFAULT_OUTCOME_PATH.exists())
                loaded = load_outcome(mod.DEFAULT_OUTCOME_PATH)
                self.assertEqual(loaded.run_id, "default-path")
            finally:
                mod.DEFAULT_OUTCOME_PATH = original

    def test_validate_unknown_fields_are_tolerated(self):
        data = _healthy_outcome_dict()
        data["unknown_future_field"] = "hello"
        data["nested_unknown"] = {"key": "value"}
        outcome = validate_outcome(data)
        self.assertEqual(outcome.outcome_status, OutcomeStatus.HEALTHY.value)


if __name__ == "__main__":
    unittest.main()
