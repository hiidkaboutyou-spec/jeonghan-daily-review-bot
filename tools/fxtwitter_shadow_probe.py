from __future__ import annotations

"""Read-only FxTwitter v2 completeness probe.

This diagnostic deliberately has no Telegram, archive, state, cursor, or delivery
integration. It answers one question only: can a no-secret provider walk every
configured source far enough to prove a requested time window is complete?

A source is considered complete only when one of these conservative conditions is met:
1. the provider cursor is exhausted, or
2. non-repost timeline rows remain chronologically non-increasing and cross below the
   requested lower boundary.

Reposts are counted but never used to prove the lower boundary because their exposed
created_timestamp can describe the original post rather than the repost event.
"""

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://api.fxtwitter.com"
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
USER_AGENT = "jeonghan-daily-review-bot/fxtwitter-shadow-probe"
DEFAULT_TIMEOUT = 10.0
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 6
DEFAULT_WORKERS = 6


@dataclass(slots=True)
class SourceResult:
    handle: str
    complete: bool = False
    status: str = "partial"
    reason: str = ""
    pages: int = 0
    rows: int = 0
    window_rows: int = 0
    repost_rows: int = 0
    schema_errors: int = 0
    cursor_exhausted: bool = False
    lower_boundary_crossed: bool = False
    chronological: bool = True
    oldest_timestamp: float | None = None
    newest_timestamp: float | None = None
    next_cursor_present: bool = False
    http_status: int | None = None


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_enabled_sources(root: Path = ROOT) -> list[dict[str, Any]]:
    base = _read_json(root / "config" / "sources.json")
    priority = _read_json(root / "config" / "jeonghan_priority_x_sources.json")
    combined = [*base.get("sources", []), *priority.get("sources", [])]

    chosen: dict[str, dict[str, Any]] = {}
    for raw in combined:
        if not isinstance(raw, dict) or not raw.get("enabled", True):
            continue
        handle = str(raw.get("handle") or "").lstrip("@").strip()
        if not HANDLE_RE.fullmatch(handle):
            raise ValueError(f"invalid configured handle: {handle!r}")
        key = handle.casefold()
        if key in chosen:
            raise ValueError(f"duplicate configured handle: @{handle}")
        chosen[key] = {
            "handle": handle,
            "include_replies": bool(raw.get("include_replies", True)),
            "mode": str(raw.get("mode") or ""),
        }
    return list(chosen.values())


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or parsed != parsed:
        return None
    return parsed


def _request_json(
    handle: str,
    *,
    cursor: str | None,
    page_size: int,
    include_replies: bool,
    timeout: float,
) -> tuple[int, dict[str, Any] | None]:
    params: dict[str, str | int] = {"count": page_size}
    if cursor:
        params["cursor"] = cursor
    if include_replies:
        params["with_replies"] = "1"
    url = f"{BASE_URL}/2/profile/{quote(handle, safe='')}/statuses?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read()
    except HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"network error: {type(exc).__name__}") from exc

    if status == 204:
        return status, None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"HTTP {status} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"HTTP {status} returned non-object JSON")
    return status, payload


def evaluate_pages(
    handle: str,
    pages: list[tuple[int, dict[str, Any] | None]],
    *,
    start_epoch: float,
    max_pages: int,
) -> SourceResult:
    """Pure evaluator used by tests and the live loop."""
    result = SourceResult(handle=handle)
    previous_authority_ts: float | None = None
    seen_cursors: set[str] = set()

    for status, payload in pages[:max_pages]:
        result.pages += 1
        result.http_status = status
        if status != 200 or not isinstance(payload, dict):
            result.status = "error"
            result.reason = f"http_{status}"
            return result
        if int(payload.get("code", status) or status) != 200:
            result.status = "error"
            result.reason = f"payload_code_{payload.get('code')}"
            return result

        rows = payload.get("results")
        cursor_obj = payload.get("cursor")
        if not isinstance(rows, list) or not isinstance(cursor_obj, dict):
            result.status = "error"
            result.reason = "invalid_schema"
            return result

        result.rows += len(rows)
        for row in rows:
            if not isinstance(row, dict):
                result.schema_errors += 1
                continue
            timestamp = _number(row.get("created_timestamp"))
            if timestamp is None:
                result.schema_errors += 1
                continue
            result.oldest_timestamp = (
                timestamp
                if result.oldest_timestamp is None
                else min(result.oldest_timestamp, timestamp)
            )
            result.newest_timestamp = (
                timestamp
                if result.newest_timestamp is None
                else max(result.newest_timestamp, timestamp)
            )
            if timestamp >= start_epoch:
                result.window_rows += 1

            if row.get("reposted_by"):
                result.repost_rows += 1
                continue

            if previous_authority_ts is not None and timestamp > previous_authority_ts:
                result.chronological = False
            previous_authority_ts = timestamp
            if result.chronological and timestamp < start_epoch:
                result.lower_boundary_crossed = True

        bottom = cursor_obj.get("bottom")
        bottom = str(bottom).strip() if bottom else ""
        result.next_cursor_present = bool(bottom)

        if not bottom:
            result.cursor_exhausted = True
            result.complete = result.schema_errors == 0
            result.status = "complete" if result.complete else "error"
            result.reason = "cursor_exhausted" if result.complete else "schema_errors"
            return result

        if bottom in seen_cursors:
            result.status = "partial"
            result.reason = "duplicate_cursor"
            return result
        seen_cursors.add(bottom)

        if result.lower_boundary_crossed and result.chronological:
            result.complete = result.schema_errors == 0
            result.status = "complete" if result.complete else "error"
            result.reason = "lower_boundary_crossed" if result.complete else "schema_errors"
            return result

    result.status = "partial"
    result.reason = "page_budget_exhausted"
    return result


def probe_source(
    source: dict[str, Any],
    *,
    start_epoch: float,
    page_size: int,
    max_pages: int,
    timeout: float,
) -> SourceResult:
    handle = str(source["handle"])
    pages: list[tuple[int, dict[str, Any] | None]] = []
    cursor: str | None = None
    seen: set[str] = set()

    for _ in range(max_pages):
        try:
            status, payload = _request_json(
                handle,
                cursor=cursor,
                page_size=page_size,
                include_replies=bool(source.get("include_replies", True)),
                timeout=timeout,
            )
        except RuntimeError as exc:
            return SourceResult(handle=handle, status="error", reason=str(exc))

        pages.append((status, payload))
        evaluated = evaluate_pages(
            handle,
            pages,
            start_epoch=start_epoch,
            max_pages=max_pages,
        )
        if evaluated.complete or evaluated.status == "error":
            return evaluated

        if not isinstance(payload, dict):
            return evaluated
        cursor_obj = payload.get("cursor")
        bottom = cursor_obj.get("bottom") if isinstance(cursor_obj, dict) else None
        next_cursor = str(bottom).strip() if bottom else ""
        if not next_cursor or next_cursor in seen:
            return evaluated
        seen.add(next_cursor)
        cursor = next_cursor

    return evaluate_pages(handle, pages, start_epoch=start_epoch, max_pages=max_pages)


def run_probe(
    *,
    lookback_hours: float,
    page_size: int,
    max_pages: int,
    timeout: float,
    workers: int = DEFAULT_WORKERS,
    root: Path = ROOT,
) -> dict[str, Any]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=lookback_hours)
    sources = load_enabled_sources(root)
    results_by_handle: dict[str, SourceResult] = {}

    # Six workers are intentionally far below the provider's documented public
    # request ceiling while preventing one blocked route from serially stalling
    # the entire 31-source diagnostic.
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(sources) or 1))) as executor:
        futures = {
            executor.submit(
                probe_source,
                source,
                start_epoch=start.timestamp(),
                page_size=page_size,
                max_pages=max_pages,
                timeout=timeout,
            ): str(source["handle"])
            for source in sources
        }
        for future in as_completed(futures):
            handle = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # fail closed without losing the other evidence
                result = SourceResult(
                    handle=handle,
                    status="error",
                    reason=f"worker_error:{type(exc).__name__}",
                )
            results_by_handle[handle.casefold()] = result
            print(
                f"@{result.handle}: {result.status} "
                f"({result.reason}; pages={result.pages}; rows={result.rows}; "
                f"window={result.window_rows})",
                flush=True,
            )

    # Keep the report stable in configured-source order regardless of completion order.
    results = [results_by_handle[str(source["handle"]).casefold()] for source in sources]
    complete = sum(item.complete for item in results)
    errors = sum(item.status == "error" for item in results)
    partial = len(results) - complete - errors
    return {
        "version": 1,
        "provider": "FxTwitter API v2",
        "base_url": BASE_URL,
        "started_window": start.isoformat(),
        "ended_window": end.isoformat(),
        "lookback_hours": lookback_hours,
        "page_size": page_size,
        "max_pages": max_pages,
        "summary": {
            "enabled_sources": len(results),
            "complete_sources": complete,
            "partial_sources": partial,
            "error_sources": errors,
            "all_sources_complete": bool(results) and complete == len(results),
        },
        "sources": [asdict(item) for item in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--output", type=Path, default=Path("fxtwitter-shadow.json"))
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)

    if args.lookback_hours <= 0:
        parser.error("--lookback-hours must be positive")
    page_size = min(100, max(1, args.page_size))
    max_pages = min(20, max(1, args.max_pages))
    timeout = min(60.0, max(1.0, args.timeout))
    workers = min(12, max(1, args.workers))

    report = run_probe(
        lookback_hours=args.lookback_hours,
        page_size=page_size,
        max_pages=max_pages,
        timeout=timeout,
        workers=workers,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], sort_keys=True), flush=True)

    if args.require_complete and not report["summary"]["all_sources_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
