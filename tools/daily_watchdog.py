from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import request

MAIN_WORKFLOW_FILE = "main.yml"
MAIN_BRANCH = "main"
LIVE_MONITOR_STEP = "Run one complete automatic monitor pass"
LIVE_EVENTS = {"schedule", "workflow_dispatch", "push"}
AUTOMATION_ACTOR = "github-actions[bot]"
ACTIVE_STATUSES = {"queued", "in_progress", "requested", "waiting", "pending"}

# Production outcome schema validation constants
OUTCOME_SCHEMA_VERSION = 1
VALID_OUTCOME_STATUSES = {"healthy", "degraded", "recovery_required", "failed"}
MAX_OUTCOME_ARTIFACT_AGE_MINUTES = 90


def log_decision(event: str, **fields: object) -> None:
    suffix = " ".join(f"{key}={value}" for key, value in sorted(fields.items()))
    print(f"daily_watchdog: {event}{(' ' + suffix) if suffix else ''}", flush=True)


def load_due_interval_minutes(path: Path = Path("config/settings.json")) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    runtime = data.get("runtime") if isinstance(data, dict) else None
    if not isinstance(runtime, dict):
        raise ValueError("runtime settings are missing")
    value = int(runtime.get("scheduled_min_interval_minutes", 0))
    if value < 1:
        raise ValueError("scheduled_min_interval_minutes must be positive")
    return value


def base_live_event(run: dict[str, Any]) -> bool:
    return (
        str(run.get("head_branch") or "") == MAIN_BRANCH
        and str(run.get("event") or "") in LIVE_EVENTS
    )


# ---------------------------------------------------------------------------
# Production outcome helpers
# ---------------------------------------------------------------------------

def validate_outcome_schema(data: dict[str, Any]) -> tuple[bool, str]:
    """Validate an outcome artifact's schema.  Returns (valid, reason)."""
    if not isinstance(data, dict):
        return False, "not_a_json_object"

    schema_version = data.get("schema_version")
    if schema_version is None:
        return False, "missing_schema_version"
    try:
        version = int(schema_version)
    except (TypeError, ValueError):
        return False, "invalid_schema_version_type"
    if version < 1 or version > OUTCOME_SCHEMA_VERSION:
        return False, f"incompatible_schema_version_{version}"

    outcome_status = data.get("outcome_status", "")
    if outcome_status not in VALID_OUTCOME_STATUSES:
        return False, f"invalid_outcome_status_{outcome_status}"

    return True, "valid"


def parse_outcome_datetime(value: object) -> datetime | None:
    """Parse an ISO datetime string from an outcome artifact."""
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def classify_outcome_health(data: dict[str, Any]) -> str:
    """Determine health classification from a validated outcome artifact.

    Returns one of: healthy, degraded, recovery_required, failed, unknown.
    """
    valid, _reason = validate_outcome_schema(data)
    if not valid:
        return "unknown"
    return str(data.get("outcome_status", "unknown"))


def outcome_recommends_recovery(data: dict[str, Any]) -> bool:
    """Check if the outcome recommends recovery dispatch."""
    recovery = data.get("recovery")
    if isinstance(recovery, dict):
        return bool(recovery.get("recovery_dispatch_recommended", False))
    return False


def outcome_useful_work_performed(data: dict[str, Any]) -> bool:
    """Check if useful work was performed according to the outcome."""
    return bool(data.get("useful_work_performed", True))


# ---------------------------------------------------------------------------
# GitHub Actions artifact fetching
# ---------------------------------------------------------------------------

class GitHubActionsClient:
    def __init__(self, repository: str, token: str, *, timeout: int = 30) -> None:
        if "/" not in repository:
            raise ValueError("GITHUB_REPOSITORY must be owner/name")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self.repository = repository
        self.token = token
        self.timeout = timeout
        self.base = f"https://api.github.com/repos/{repository}"

    def _request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> Any:
        body = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "jeonghan-daily-watchdog",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=headers, method=method)
        with request.urlopen(req, timeout=self.timeout) as response:
            raw = response.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))

    def list_daily_runs(self) -> list[dict[str, Any]]:
        url = (
            f"{self.base}/actions/workflows/{MAIN_WORKFLOW_FILE}/runs"
            f"?branch={MAIN_BRANCH}&per_page=100"
        )
        payload = self._request("GET", url)
        runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
        if not isinstance(runs, list):
            raise RuntimeError("GitHub workflow-runs response is malformed")
        return [item for item in runs if isinstance(item, dict)]

    def run_executed_live_monitor(self, run_id: int) -> bool:
        url = f"{self.base}/actions/runs/{run_id}/jobs?per_page=100"
        payload = self._request("GET", url)
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            raise RuntimeError("GitHub workflow-jobs response is malformed")
        for job in jobs:
            if not isinstance(job, dict):
                continue
            steps = job.get("steps")
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, dict) or step.get("name") != LIVE_MONITOR_STEP:
                    continue
                return str(step.get("conclusion") or "") != "skipped"
        return False

    def dispatch_live(self) -> None:
        url = f"{self.base}/actions/workflows/{MAIN_WORKFLOW_FILE}/dispatches"
        self._request(
            "POST",
            url,
            {"ref": MAIN_BRANCH, "inputs": {"mode": "live"}},
        )

    def fetch_latest_production_outcome(self, run_id: int) -> dict[str, Any] | None:
        """Download and parse the production-outcome artifact from a workflow run.

        Returns the parsed outcome dict, or None if unavailable/invalid.
        """
        try:
            url = (
                f"{self.base}/actions/runs/{run_id}/artifacts"
                f"?per_page=30"
            )
            payload = self._request("GET", url)
            artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
            if not isinstance(artifacts, list):
                return None

            outcome_artifact = None
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                if str(artifact.get("name") or "") == "production-outcome":
                    outcome_artifact = artifact
                    break

            if outcome_artifact is None:
                return None

            # Check artifact age
            created_at = parse_outcome_datetime(outcome_artifact.get("created_at"))
            if created_at is not None:
                age_minutes = (
                    datetime.now(timezone.utc) - created_at
                ).total_seconds() / 60
                if age_minutes > MAX_OUTCOME_ARTIFACT_AGE_MINUTES:
                    log_decision(
                        "outcome_stale",
                        run_id=run_id,
                        age_minutes=int(age_minutes),
                    )
                    return None

            # Download the artifact zip
            artifact_id = outcome_artifact.get("id")
            if not artifact_id:
                return None
            zip_url = f"{self.base}/actions/artifacts/{artifact_id}/zip"
            # We need raw bytes for the zip
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "jeonghan-daily-watchdog",
            }
            req = request.Request(zip_url, headers=headers, method="GET")
            with request.urlopen(req, timeout=self.timeout) as response:
                zip_data = response.read()

            if not zip_data:
                return None

            # Extract production-outcome.json from the zip in memory
            import zipfile
            zip_buffer = io.BytesIO(zip_data)
            with zipfile.ZipFile(zip_buffer, "r") as zf:
                if "production-outcome.json" not in zf.namelist():
                    return None
                raw = zf.read("production-outcome.json")
                return json.loads(raw.decode("utf-8"))

        except Exception as exc:
            log_decision(
                "outcome_fetch_failed",
                error=type(exc).__name__,
                run_id=run_id,
            )
            return None


def source_is_live(client: Any, *, run_id: int, event: str) -> bool:
    if event in {"schedule", "push"}:
        return True
    if event != "workflow_dispatch":
        return False
    return bool(client.run_executed_live_monitor(run_id))


def newer_covering_run(
    client: Any,
    runs: list[dict[str, Any]],
    *,
    source_run_number: int,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for run in runs:
        try:
            run_number = int(run.get("run_number") or 0)
            run_id = int(run.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if run_number <= source_run_number or not base_live_event(run):
            continue
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status in ACTIVE_STATUSES:
            candidates.append(run)
            continue
        if status != "completed" or conclusion != "success":
            continue
        if str(run.get("event") or "") == "workflow_dispatch":
            if run_id <= 0 or not client.run_executed_live_monitor(run_id):
                continue
        candidates.append(run)
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("run_number") or 0))


def _parse_github_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _outcome_based_recovery_decision(
    client: Any,
    source_run_id: int,
    *,
    outcome: dict[str, Any],
    source_conclusion: str,
) -> str | None:
    """Determine if recovery should be dispatched based on the structured outcome.

    Returns a recovery action string:
    - "dispatch" → dispatch a recovery run
    - "skip" → do not dispatch (already handled or unrecoverable)
    - None → no outcome-based decision (fall through to legacy logic)
    """
    valid, reason = validate_outcome_schema(outcome)
    if not valid:
        log_decision(
            "outcome_invalid",
            source_run_id=source_run_id,
            reason=reason,
        )
        return None

    status = classify_outcome_health(outcome)
    useful = outcome_useful_work_performed(outcome)
    recommends = outcome_recommends_recovery(outcome)

    log_decision(
        "outcome_consumed",
        source_run_id=source_run_id,
        status=status,
        useful_work=useful,
        recommends_recovery=recommends,
    )

    # HEALTHY with useful work → no recovery
    if status == "healthy" and useful:
        log_decision(
            "outcome_healthy",
            source_run_id=source_run_id,
        )
        return "skip"

    # HEALTHY without useful work → check if workflow also succeeded
    if status == "healthy" and not useful:
        # Valid zero-update window — this is normal
        log_decision(
            "outcome_healthy_zero_update",
            source_run_id=source_run_id,
        )
        return "skip"

    # FAILED → do not blindly loop; only dispatch if explicitly recoverable
    if status == "failed":
        reasons = outcome.get("outcome_reasons", [])
        # State checkpoint failure is not self-recoverable
        if "state_checkpoint_failed" in reasons:
            log_decision(
                "outcome_failed_not_recoverable",
                source_run_id=source_run_id,
                reason="state_checkpoint_failed",
            )
            return "skip"
        # All sources failed but may be transient — allow one retry
        if "all_active_sources_failed" in reasons:
            log_decision(
                "outcome_failed_dispatch_recommended",
                source_run_id=source_run_id,
                reason="all_active_sources_failed",
            )
            return "dispatch"
        # Unknown failure — safe default is skip
        log_decision(
            "outcome_failed_skip",
            source_run_id=source_run_id,
            reasons=reasons,
        )
        return "skip"

    # RECOVERY_REQUIRED → dispatch if recommended
    if status == "recovery_required":
        if recommends:
            log_decision(
                "outcome_recovery_required_dispatch",
                source_run_id=source_run_id,
            )
            return "dispatch"
        log_decision(
            "outcome_recovery_required_no_dispatch",
            source_run_id=source_run_id,
        )
        return "skip"

    # DEGRADED → no immediate recovery needed
    if status == "degraded":
        log_decision(
            "outcome_degraded_no_recovery",
            source_run_id=source_run_id,
        )
        return "skip"

    # Unknown status → fall through to legacy logic
    return None


def run_periodic_watchdog(
    client: Any,
    *,
    interval_minutes: int,
    now: datetime | None = None,
) -> int:
    """Recover a stale Daily even when no workflow_run event was emitted."""
    if interval_minutes < 1:
        log_decision("guard_blocked", reason="invalid_interval", trigger="schedule")
        return 1
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        runs = sorted(
            (run for run in client.list_daily_runs() if base_live_event(run)),
            key=lambda item: int(item.get("run_number") or 0),
            reverse=True,
        )
        live_runs: list[dict[str, Any]] = []
        for run in runs:
            status = str(run.get("status") or "")
            event = str(run.get("event") or "")
            if status in ACTIVE_STATUSES:
                log_decision(
                    "successor_not_needed",
                    reason="active_daily",
                    run_id=run.get("id", ""),
                    status=status,
                )
                return 0
            if event == "workflow_dispatch":
                run_id = int(run.get("id") or 0)
                if run_id <= 0 or not client.run_executed_live_monitor(run_id):
                    continue
            live_runs.append(run)
    except Exception as exc:
        log_decision("lookup_failed", error=type(exc).__name__, phase="periodic_run_check")
        return 1

    latest = live_runs[0] if live_runs else None
    if latest is not None:
        actor = latest.get("actor")
        actor_login = str(actor.get("login") or "") if isinstance(actor, dict) else str(actor or "")
        if (
            str(latest.get("event") or "") == "workflow_dispatch"
            and actor_login == AUTOMATION_ACTOR
            and str(latest.get("conclusion") or "") != "success"
        ):
            log_decision(
                "guard_blocked",
                reason="failed_automated_recovery",
                source_run_id=latest.get("id", ""),
            )
            return 0
        stamp = _parse_github_time(latest.get("updated_at") or latest.get("created_at"))
        if stamp is None:
            log_decision("lookup_failed", phase="periodic_timestamp", source_run_id=latest.get("id", ""))
            return 1
        if (now - stamp).total_seconds() < interval_minutes * 60:
            log_decision(
                "successor_not_needed",
                reason="recent_daily",
                source_run_id=latest.get("id", ""),
            )
            return 0

    try:
        client.dispatch_live()
    except Exception as exc:
        log_decision("dispatch_failed", error=type(exc).__name__, trigger="schedule")
        return 1
    log_decision("successor_dispatched", trigger="schedule")
    return 0


def run_watchdog(
    client: Any,
    *,
    source_run_id: int,
    source_run_number: int,
    source_event: str,
    source_conclusion: str,
    source_actor: str,
    interval_minutes: int,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    if source_event not in LIVE_EVENTS:
        log_decision("guard_blocked", reason="source_event", source_run_id=source_run_id)
        return 0
    try:
        live_source = source_is_live(client, run_id=source_run_id, event=source_event)
    except Exception as exc:
        log_decision(
            "lookup_failed",
            error=type(exc).__name__,
            phase="source_live_check",
            source_run_id=source_run_id,
        )
        return 1
    if not live_source:
        log_decision("guard_blocked", reason="source_not_live", source_run_id=source_run_id)
        return 0
    if interval_minutes < 1:
        log_decision("guard_blocked", reason="invalid_interval", source_run_id=source_run_id)
        return 1
    if (
        source_event == "workflow_dispatch"
        and source_actor == AUTOMATION_ACTOR
        and source_conclusion != "success"
    ):
        log_decision(
            "guard_blocked",
            reason="failed_automated_recovery",
            source_run_id=source_run_id,
        )
        return 0

    # --- Structured outcome consumption ---
    # Attempt to fetch the production outcome artifact from the source run
    outcome_decision = None
    try:
        outcome = client.fetch_latest_production_outcome(source_run_id)
        if outcome is not None:
            outcome_decision = _outcome_based_recovery_decision(
                client,
                source_run_id,
                outcome=outcome,
                source_conclusion=source_conclusion,
            )
    except Exception as exc:
        log_decision(
            "outcome_fetch_error",
            error=type(exc).__name__,
            source_run_id=source_run_id,
        )

    # If outcome-based decision says to skip, respect it
    if outcome_decision == "skip":
        log_decision(
            "outcome_skip_recovery",
            source_run_id=source_run_id,
        )
        return 0

    # If outcome-based decision says to dispatch, proceed directly to dispatch
    # (skip the sleep-and-check cycle since the outcome already confirmed the need)
    if outcome_decision == "dispatch":
        log_decision(
            "armed",
            interval_minutes=interval_minutes,
            source_run_id=source_run_id,
            source_run_number=source_run_number,
            outcome_based=True,
        )
        # Still sleep briefly to avoid dispatching during a race
        sleep_fn(min(60, interval_minutes * 60))

        # Check for a newer run before dispatching
        try:
            runs = client.list_daily_runs()
            newer = newer_covering_run(client, runs, source_run_number=source_run_number)
        except Exception as exc:
            log_decision(
                "lookup_failed",
                error=type(exc).__name__,
                phase="newer_run_check",
                source_run_id=source_run_id,
            )
            return 1

        if newer is not None:
            log_decision(
                "newer_run_exists",
                run_id=newer.get("id", ""),
                run_number=newer.get("run_number", ""),
                status=newer.get("status", ""),
            )
            log_decision("successor_not_needed", source_run_id=source_run_id)
            return 0

        try:
            client.dispatch_live()
        except Exception as exc:
            log_decision(
                "dispatch_failed",
                error=type(exc).__name__,
                source_run_id=source_run_id,
            )
            return 1

        log_decision("successor_dispatched", source_run_id=source_run_id, outcome_based=True)
        return 0

    # --- Legacy watchdog behavior (outcome unavailable or inconclusive) ---
    log_decision(
        "armed",
        interval_minutes=interval_minutes,
        source_run_id=source_run_id,
        source_run_number=source_run_number,
    )
    sleep_fn(interval_minutes * 60)

    try:
        runs = client.list_daily_runs()
        newer = newer_covering_run(client, runs, source_run_number=source_run_number)
    except Exception as exc:
        log_decision(
            "lookup_failed",
            error=type(exc).__name__,
            phase="newer_run_check",
            source_run_id=source_run_id,
        )
        return 1

    if newer is not None:
        log_decision(
            "newer_run_exists",
            run_id=newer.get("id", ""),
            run_number=newer.get("run_number", ""),
            status=newer.get("status", ""),
        )
        log_decision("successor_not_needed", source_run_id=source_run_id)
        return 0

    try:
        client.dispatch_live()
    except Exception as exc:
        log_decision(
            "dispatch_failed",
            error=type(exc).__name__,
            source_run_id=source_run_id,
        )
        return 1

    log_decision("successor_dispatched", source_run_id=source_run_id)
    return 0


def main() -> int:
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        token = os.environ["GITHUB_TOKEN"]
        interval = load_due_interval_minutes()
        client = GitHubActionsClient(repository, token)
        if os.environ.get("WATCHDOG_TRIGGER", "workflow_run") == "schedule":
            return run_periodic_watchdog(client, interval_minutes=interval)
        source_run_id = int(os.environ["SOURCE_RUN_ID"])
        source_run_number = int(os.environ["SOURCE_RUN_NUMBER"])
        source_event = os.environ.get("SOURCE_EVENT", "")
        source_conclusion = os.environ.get("SOURCE_CONCLUSION", "")
        source_actor = os.environ.get("SOURCE_ACTOR", "")
    except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
        log_decision("guard_blocked", reason=type(exc).__name__)
        return 1

    return run_watchdog(
        client,
        source_run_id=source_run_id,
        source_run_number=source_run_number,
        source_event=source_event,
        source_conclusion=source_conclusion,
        source_actor=source_actor,
        interval_minutes=interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
