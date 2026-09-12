from __future__ import annotations

"""Run the Daily watchdog with a safe cross-origin artifact download.

GitHub's artifact download endpoint redirects to a signed blob URL. urllib's
default redirect handler can forward the GitHub Authorization header to that
different host; the signed host may reject that request. This wrapper follows
the redirect explicitly without forwarding GitHub credentials.
"""

import io
import json
import time
import zipfile
from datetime import datetime, timezone
from urllib import error, request

try:
    from tools import daily_watchdog as _watchdog
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog as _watchdog


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _download_zip_without_cross_origin_auth(client, artifact_id: int) -> bytes:
    zip_url = f"{client.base}/actions/artifacts/{artifact_id}/zip"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {client.token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "jeonghan-daily-watchdog",
    }
    req = request.Request(zip_url, headers=headers, method="GET")
    opener = request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=client.timeout) as response:
            return response.read()
    except error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise
        location = exc.headers.get("Location")
        if not location:
            raise
        redirected = request.Request(
            location,
            headers={"User-Agent": "jeonghan-daily-watchdog"},
            method="GET",
        )
        with request.urlopen(redirected, timeout=client.timeout) as response:
            return response.read()


def _fetch_latest_production_outcome(self, run_id: int):
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            payload = self._request(
                "GET",
                f"{self.base}/actions/runs/{run_id}/artifacts?per_page=30",
            )
            artifacts = payload.get("artifacts") if isinstance(payload, dict) else None
            if not isinstance(artifacts, list):
                return None

            outcome_artifact = next(
                (
                    artifact
                    for artifact in artifacts
                    if isinstance(artifact, dict)
                    and str(artifact.get("name") or "") == "production-outcome"
                ),
                None,
            )
            if outcome_artifact is None:
                if attempt < 3:
                    time.sleep(1)
                    continue
                return None

            created_at = _watchdog.parse_outcome_datetime(outcome_artifact.get("created_at"))
            if created_at is not None:
                age_minutes = (
                    datetime.now(timezone.utc) - created_at
                ).total_seconds() / 60
                if age_minutes > _watchdog.MAX_OUTCOME_ARTIFACT_AGE_MINUTES:
                    _watchdog.log_decision(
                        "outcome_stale",
                        run_id=run_id,
                        age_minutes=int(age_minutes),
                    )
                    return None

            artifact_id = int(outcome_artifact.get("id") or 0)
            if artifact_id <= 0:
                return None
            zip_data = _download_zip_without_cross_origin_auth(self, artifact_id)
            if not zip_data:
                return None

            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as archive:
                if "production-outcome.json" not in archive.namelist():
                    return None
                return json.loads(
                    archive.read("production-outcome.json").decode("utf-8")
                )
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1)
                continue

    _watchdog.log_decision(
        "outcome_fetch_failed",
        error=type(last_error).__name__ if last_error is not None else "UnknownError",
        http_status=getattr(last_error, "code", "") if last_error is not None else "",
        run_id=run_id,
    )
    return None


_watchdog.GitHubActionsClient.fetch_latest_production_outcome = (
    _fetch_latest_production_outcome
)


if __name__ == "__main__":
    raise SystemExit(_watchdog.main())
