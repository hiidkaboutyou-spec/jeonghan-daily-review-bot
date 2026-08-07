from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .x_client import XCollectionError, XCollector


@dataclass(frozen=True, slots=True)
class SourceHealth:
    source: str
    last_success: str
    last_failure: str
    consecutive_failures: int
    recent_result_count: int
    last_latency_ms: int
    last_error_code: str

    def status(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        if self.consecutive_failures >= 3:
            return "unhealthy"
        if not self.last_success:
            return "unknown"
        try:
            value = datetime.fromisoformat(self.last_success.replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        except ValueError:
            return "unknown"
        if (now - value.astimezone(timezone.utc)).total_seconds() > 24 * 3600:
            return "stale"
        return "healthy"


class SourceHealthStore:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_health (
                source TEXT PRIMARY KEY,
                last_success TEXT NOT NULL DEFAULT '',
                last_failure TEXT NOT NULL DEFAULT '',
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                recent_result_count INTEGER NOT NULL DEFAULT 0,
                last_latency_ms INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.commit()

    def success(self, source: str, count: int, latency_ms: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO source_health(source,last_success,consecutive_failures,recent_result_count,last_latency_ms,last_error_code)
                VALUES(?,?,0,?,?, '')
                ON CONFLICT(source) DO UPDATE SET
                    last_success=excluded.last_success,
                    consecutive_failures=0,
                    recent_result_count=excluded.recent_result_count,
                    last_latency_ms=excluded.last_latency_ms,
                    last_error_code=''
                """,
                (source.lower(), now, max(0, int(count)), max(0, int(latency_ms))),
            )

    def failure(self, source: str, error_code: str, latency_ms: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        safe_code = "".join(ch for ch in str(error_code) if ch.isalnum() or ch in "_-")[:80] or "Error"
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO source_health(source,last_failure,consecutive_failures,recent_result_count,last_latency_ms,last_error_code)
                VALUES(?,?,1,0,?,?)
                ON CONFLICT(source) DO UPDATE SET
                    last_failure=excluded.last_failure,
                    consecutive_failures=source_health.consecutive_failures+1,
                    recent_result_count=0,
                    last_latency_ms=excluded.last_latency_ms,
                    last_error_code=excluded.last_error_code
                """,
                (source.lower(), now, max(0, int(latency_ms)), safe_code),
            )

    def list_all(self) -> list[SourceHealth]:
        rows = self.conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
        return [_row(row) for row in rows]

    def get(self, source: str) -> SourceHealth | None:
        row = self.conn.execute("SELECT * FROM source_health WHERE source=?", (source.lower(),)).fetchone()
        return _row(row) if row is not None else None

    def close(self) -> None:
        self.conn.close()


class HealthTrackingXCollector(XCollector):
    """Existing X collector with sanitized per-source telemetry only."""

    def __init__(self, cookies, sources, keyword_groups, health: SourceHealthStore):
        super().__init__(cookies, sources, keyword_groups)
        self.health = health

    async def _collect_source_timeline(self, handle, start, end, *, limit, include_replies):
        started = time.monotonic()
        try:
            result = await super()._collect_source_timeline(
                handle, start, end, limit=limit, include_replies=include_replies
            )
        except XCollectionError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            # Store only the technical exception class, never raw auth-bearing text.
            self.health.failure(handle, type(exc).__name__, elapsed)
            raise
        elapsed = int((time.monotonic() - started) * 1000)
        self.health.success(handle, len(result), elapsed)
        return result


def _row(row: sqlite3.Row) -> SourceHealth:
    return SourceHealth(
        source=str(row["source"]),
        last_success=str(row["last_success"]),
        last_failure=str(row["last_failure"]),
        consecutive_failures=int(row["consecutive_failures"]),
        recent_result_count=int(row["recent_result_count"]),
        last_latency_ms=int(row["last_latency_ms"]),
        last_error_code=str(row["last_error_code"]),
    )
