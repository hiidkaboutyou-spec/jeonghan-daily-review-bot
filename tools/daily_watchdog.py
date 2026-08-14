from __future__ import annotations

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
    return str(run.get("head_branch") or "") == MAIN_BRANCH and str(run.get("event") or "") in LIVE_EVENTS


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
        url = f"{self.base}/actions/workflows/{MAIN_WORKFLOW_FILE}/runs?branch={MAIN_BRANCH}&per_page=100"
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
                if isinstance(step, dict) and step.get("name") == LIVE_MONITOR_STEP:
                    return str(step.get("conclusion") or "") != "skipped"
        return False

    def dispatch_live(self) -> None:
        url = f"{self.base}/actions/workflows/{MAIN_WORKFLOW_FILE}/dispatches"
        self._request("POST", url, {"ref": MAIN_BRANCH, "inputs": {"mode": "live"}})


def source_is_live(client: Any, *, run_id: int, event: str) -> bool:
    if event in {"schedule", "push"}:
        return True
    if event != "workflow_dispatch":
        return False
    return bool(client.run_executed_live_monitor(run_id))


def newer_covering_run(client: Any, runs: list[dict[str, Any]], *, source_run_number: int) -> dict[str, Any] | None:
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
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def run_periodic_watchdog(client: Any, *, interval_minutes: int, now: datetime | None = None) -> int:
    if interval_minutes < 1:
        log_decision("guard_blocked", reason="invalid_interval")
        return 1
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        runs = client.list_daily_runs()
    except Exception as exc:
        log_decision("lookup_failed", error=type(exc).__name__, phase="periodic_run_check")
        return 1

    live_runs = [run for run in runs if base_live_event(run)]
    live_runs.sort(key=lambda item: int(item.get("run_number") or 0), reverse=True)
    for run in live_runs:
        status = str(run.get("status") or "")
        if status in ACTIVE_STATUSES:
            log_decision("newer_run_exists", run_id=run.get("id", ""), run_number=run.get("run_number", ""), status=status)
            log_decision("successor_not_needed", reason="active_daily")
            return 0

    latest = live_runs[0] if live_runs else None
    if latest is not None:
        event = str(latest.get("event") or "")
        actor = str((latest.get("actor") or {}).get("login") or latest.get("actor_login") or "")
        status = str(latest.get("status") or "")
        conclusion = str(latest.get("conclusion") or "")
        if event == "workflow_dispatch" and actor == AUTOMATION_ACTOR and status == "completed" and conclusion != "success":
            log_decision("guard_blocked", reason="failed_automated_recovery", source_run_id=latest.get("id", ""))
            return 0

        stamp = _parse_github_time(latest.get("updated_at") or latest.get("created_at"))
        if stamp is None:
            log_decision("lookup_failed", phase="periodic_timestamp", source_run_id=latest.get("id", ""))
            return 1
        age_seconds = (now - stamp).total_seconds()
        if age_seconds < interval_minutes * 60:
            log_decision("successor_not_needed", reason="recent_daily", source_run_id=latest.get("id", ""))
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
        log_decision("lookup_failed", error=type(exc).__name__, phase="source_live_check", source_run_id=source_run_id)
        return 1
    if not live_source:
        log_decision("guard_blocked", reason="source_not_live", source_run_id=source_run_id)
        return 0
    if interval_minutes < 1:
        log_decision("guard_blocked", reason="invalid_interval", source_run_id=source_run_id)
        return 1
    if source_event == "workflow_dispatch" and source_actor == AUTOMATION_ACTOR and source_conclusion != "success":
        log_decision("guard_blocked", reason="failed_automated_recovery", source_run_id=source_run_id)
        return 0

    log_decision("armed", interval_minutes=interval_minutes, source_run_id=source_run_id, source_run_number=source_run_number)
    sleep_fn(interval_minutes * 60)
    try:
        runs = client.list_daily_runs()
        newer = newer_covering_run(client, runs, source_run_number=source_run_number)
    except Exception as exc:
        log_decision("lookup_failed", error=type(exc).__name__, phase="newer_run_check", source_run_id=source_run_id)
        return 1
    if newer is not None:
        log_decision("newer_run_exists", run_id=newer.get("id", ""), run_number=newer.get("run_number", ""), status=newer.get("status", ""))
        log_decision("successor_not_needed", source_run_id=source_run_id)
        return 0
    try:
        client.dispatch_live()
    except Exception as exc:
        log_decision("dispatch_failed", error=type(exc).__name__, source_run_id=source_run_id)
        return 1
    log_decision("successor_dispatched", source_run_id=source_run_id)
    return 0


def main() -> int:
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        token = os.environ["GITHUB_TOKEN"]
        interval = load_due_interval_minutes()
        client = GitHubActionsClient(repository, token)
        trigger = os.environ.get("WATCHDOG_TRIGGER", "workflow_run")
        if trigger == "schedule":
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
