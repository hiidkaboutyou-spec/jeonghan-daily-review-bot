from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class SourceWindowStatus(str, Enum):
    ATTEMPTING = "attempting"
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNPROVEN = "unproven"


class SourceLedgerError(RuntimeError):
    """Per-source completeness truth could not be persisted safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_handle(value: str) -> str:
    return str(value or "").lstrip("@").strip().casefold()


@dataclass(frozen=True, slots=True)
class SourceWindowResult:
    source_handle: str
    window_start: str
    window_end: str
    status: SourceWindowStatus
    attempt_id: str = ""
    raw_observation_count: int = 0
    retained_count: int = 0
    retry_count: int = 0
    error_class: str = ""
    error_summary: str = ""
    provider_cursor: str = ""
    proof_kind: str = ""

    def __post_init__(self) -> None:
        handle = _norm_handle(self.source_handle)
        if not handle or not self.window_start or not self.window_end:
            raise SourceLedgerError("Source ledger rows require source and window bounds.")
        object.__setattr__(self, "source_handle", handle)


class SourceLedgerStore:
    """Durable source-by-source completeness, retry and cursor ledger.

    A COMPLETE row may advance only that source's durable watermark. PARTIAL and
    UNPROVEN rows are recorded but never advance it. This makes one failing source
    independent from all other configured sources.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(self.path, timeout=15)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        except sqlite3.Error as exc:
            raise SourceLedgerError(
                f"Could not initialize source ledger: {type(exc).__name__}"
            ) from exc

    def _init_schema(self) -> None:
        try:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_ledger_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_windows (
                    source_handle TEXT NOT NULL,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_id TEXT NOT NULL DEFAULT '',
                    raw_observation_count INTEGER NOT NULL DEFAULT 0,
                    retained_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error_class TEXT NOT NULL DEFAULT '',
                    error_summary TEXT NOT NULL DEFAULT '',
                    provider_cursor TEXT NOT NULL DEFAULT '',
                    proof_kind TEXT NOT NULL DEFAULT '',
                    first_attempted_at TEXT NOT NULL,
                    last_attempted_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(source_handle, window_start, window_end)
                );

                CREATE INDEX IF NOT EXISTS source_windows_status_idx
                ON source_windows(status, last_attempted_at);

                CREATE INDEX IF NOT EXISTS source_windows_source_idx
                ON source_windows(source_handle, window_end);

                CREATE TABLE IF NOT EXISTS source_cursors (
                    source_handle TEXT PRIMARY KEY,
                    complete_through TEXT NOT NULL DEFAULT '',
                    provider_cursor TEXT NOT NULL DEFAULT '',
                    last_complete_window_start TEXT NOT NULL DEFAULT '',
                    last_complete_window_end TEXT NOT NULL DEFAULT '',
                    last_status TEXT NOT NULL DEFAULT '',
                    last_attempt_id TEXT NOT NULL DEFAULT '',
                    last_error_class TEXT NOT NULL DEFAULT '',
                    last_error_summary TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            self.conn.execute(
                """
                INSERT INTO source_ledger_meta(key, value)
                VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            raise SourceLedgerError(
                f"Could not create source ledger schema: {type(exc).__name__}"
            ) from exc

    def start_attempt(
        self,
        *,
        source_handle: str,
        window_start: str,
        window_end: str,
        attempt_id: str = "",
    ) -> None:
        now = _utc_now()
        handle = _norm_handle(source_handle)
        if not handle:
            raise SourceLedgerError("Source handle is required.")
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO source_windows(
                        source_handle, window_start, window_end, status,
                        attempt_id, first_attempted_at, last_attempted_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(source_handle, window_start, window_end) DO UPDATE SET
                        status=excluded.status,
                        attempt_id=excluded.attempt_id,
                        last_attempted_at=excluded.last_attempted_at,
                        attempt_count=source_windows.attempt_count + 1,
                        retry_count=source_windows.retry_count + 1,
                        error_class='',
                        error_summary=''
                    """,
                    (
                        handle,
                        str(window_start),
                        str(window_end),
                        SourceWindowStatus.ATTEMPTING.value,
                        str(attempt_id or ""),
                        now,
                        now,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT INTO source_cursors(
                        source_handle, last_status, last_attempt_id, updated_at
                    ) VALUES(?,?,?,?)
                    ON CONFLICT(source_handle) DO UPDATE SET
                        last_status=excluded.last_status,
                        last_attempt_id=excluded.last_attempt_id,
                        updated_at=excluded.updated_at
                    """,
                    (handle, SourceWindowStatus.ATTEMPTING.value, str(attempt_id or ""), now),
                )
        except sqlite3.Error as exc:
            raise SourceLedgerError(
                f"Could not start source ledger attempt: {type(exc).__name__}"
            ) from exc

    def finish(self, result: SourceWindowResult) -> None:
        now = _utc_now()
        complete = result.status is SourceWindowStatus.COMPLETE
        completed_at = now if complete else ""
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO source_windows(
                        source_handle, window_start, window_end, status, attempt_id,
                        raw_observation_count, retained_count, retry_count,
                        error_class, error_summary, provider_cursor, proof_kind,
                        first_attempted_at, last_attempted_at, completed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_handle, window_start, window_end) DO UPDATE SET
                        status=excluded.status,
                        attempt_id=excluded.attempt_id,
                        raw_observation_count=excluded.raw_observation_count,
                        retained_count=excluded.retained_count,
                        retry_count=MAX(source_windows.retry_count, excluded.retry_count),
                        error_class=excluded.error_class,
                        error_summary=excluded.error_summary,
                        provider_cursor=excluded.provider_cursor,
                        proof_kind=excluded.proof_kind,
                        last_attempted_at=excluded.last_attempted_at,
                        completed_at=excluded.completed_at
                    """,
                    (
                        result.source_handle,
                        result.window_start,
                        result.window_end,
                        result.status.value,
                        result.attempt_id,
                        max(0, int(result.raw_observation_count)),
                        max(0, int(result.retained_count)),
                        max(0, int(result.retry_count)),
                        str(result.error_class or "")[:160],
                        str(result.error_summary or "")[:1000],
                        str(result.provider_cursor or "")[:4096],
                        str(result.proof_kind or "")[:120],
                        now,
                        now,
                        completed_at,
                    ),
                )

                if complete:
                    self.conn.execute(
                        """
                        INSERT INTO source_cursors(
                            source_handle, complete_through, provider_cursor,
                            last_complete_window_start, last_complete_window_end,
                            last_status, last_attempt_id, last_error_class,
                            last_error_summary, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source_handle) DO UPDATE SET
                            provider_cursor=CASE
                                WHEN source_cursors.complete_through='' OR excluded.complete_through >= source_cursors.complete_through
                                THEN excluded.provider_cursor ELSE source_cursors.provider_cursor END,
                            last_complete_window_start=CASE
                                WHEN source_cursors.complete_through='' OR excluded.complete_through >= source_cursors.complete_through
                                THEN excluded.last_complete_window_start ELSE source_cursors.last_complete_window_start END,
                            last_complete_window_end=CASE
                                WHEN source_cursors.complete_through='' OR excluded.complete_through >= source_cursors.complete_through
                                THEN excluded.last_complete_window_end ELSE source_cursors.last_complete_window_end END,
                            complete_through=CASE
                                WHEN source_cursors.complete_through='' OR excluded.complete_through > source_cursors.complete_through
                                THEN excluded.complete_through ELSE source_cursors.complete_through END,
                            last_status=excluded.last_status,
                            last_attempt_id=excluded.last_attempt_id,
                            last_error_class='',
                            last_error_summary='',
                            updated_at=excluded.updated_at
                        """,
                        (
                            result.source_handle,
                            result.window_end,
                            result.provider_cursor,
                            result.window_start,
                            result.window_end,
                            result.status.value,
                            result.attempt_id,
                            "",
                            "",
                            now,
                        ),
                    )
                else:
                    self.conn.execute(
                        """
                        INSERT INTO source_cursors(
                            source_handle, last_status, last_attempt_id,
                            last_error_class, last_error_summary, updated_at
                        ) VALUES(?,?,?,?,?,?)
                        ON CONFLICT(source_handle) DO UPDATE SET
                            last_status=excluded.last_status,
                            last_attempt_id=excluded.last_attempt_id,
                            last_error_class=excluded.last_error_class,
                            last_error_summary=excluded.last_error_summary,
                            updated_at=excluded.updated_at
                        """,
                        (
                            result.source_handle,
                            result.status.value,
                            result.attempt_id,
                            str(result.error_class or "")[:160],
                            str(result.error_summary or "")[:1000],
                            now,
                        ),
                    )
        except sqlite3.Error as exc:
            raise SourceLedgerError(
                f"Could not finish source ledger result: {type(exc).__name__}"
            ) from exc

    def cursor(self, source_handle: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM source_cursors WHERE source_handle=?",
            (_norm_handle(source_handle),),
        ).fetchone()
        return dict(row) if row is not None else None

    def window(self, source_handle: str, window_start: str, window_end: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM source_windows
            WHERE source_handle=? AND window_start=? AND window_end=?
            """,
            (_norm_handle(source_handle), str(window_start), str(window_end)),
        ).fetchone()
        return dict(row) if row is not None else None

    def source_statuses(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM source_cursors ORDER BY source_handle"
        ).fetchall()]

    def close(self) -> None:
        self.conn.close()
