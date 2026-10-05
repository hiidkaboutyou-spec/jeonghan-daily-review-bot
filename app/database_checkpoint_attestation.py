"""Workflow-side SQLite checkpoint attestation.

This helper runs after the application has emitted production-outcome.json.
It records an attempted checkpoint before touching SQLite, fails closed on
integrity/checkpoint problems, and marks success only after a complete
TRUNCATE checkpoint.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from .production_outcome import (
    OutcomeValidationError,
    classify_outcome,
    load_outcome,
    save_outcome,
)


def _persist_checkpoint_state(outcome, outcome_path: Path, *, success: bool) -> None:
    outcome.state.database_checkpoint_attempted = True
    outcome.state.database_checkpoint_success = success
    outcome.outcome_status, outcome.outcome_reasons = classify_outcome(outcome)
    save_outcome(outcome, outcome_path)


def attest_database_checkpoint(database_path: Path, outcome_path: Path) -> None:
    """Attest a complete SQLite checkpoint or raise a bounded RuntimeError."""
    try:
        outcome = load_outcome(outcome_path)
    except OutcomeValidationError as exc:
        raise RuntimeError("production outcome is missing or invalid") from exc

    # Persist the fail-closed state before opening SQLite. If the process dies
    # during quick_check/checkpoint, the uploaded artifact remains a failure.
    _persist_checkpoint_state(outcome, outcome_path, success=False)

    if not database_path.is_file():
        raise RuntimeError("database file is missing")

    connection: sqlite3.Connection | None = None
    try:
        # mode=rw prevents a typo/missing path from silently creating a DB.
        uri = f"{database_path.resolve().as_uri()}?mode=rw"
        connection = sqlite3.connect(uri, uri=True, timeout=30.0)

        quick_rows = connection.execute("PRAGMA quick_check").fetchall()
        if quick_rows != [("ok",)]:
            raise RuntimeError("database quick_check failed")

        row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if row is None or len(row) < 3:
            raise RuntimeError("database checkpoint returned an invalid result")

        busy, log_frames, checkpointed_frames = (int(row[0]), int(row[1]), int(row[2]))
        if busy != 0 or checkpointed_frames != log_frames:
            raise RuntimeError("database checkpoint was busy or partial")
    except sqlite3.Error as exc:
        raise RuntimeError("database checkpoint failed") from exc
    finally:
        if connection is not None:
            connection.close()

    _persist_checkpoint_state(outcome, outcome_path, success=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Attest the production SQLite checkpoint")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--outcome",
        type=Path,
        default=Path("production-outcome.json"),
    )
    args = parser.parse_args()

    try:
        attest_database_checkpoint(args.database, args.outcome)
    except RuntimeError as exc:
        # Deliberately bounded: do not print raw SQLite paths/SQL/exception text.
        print(f"checkpoint attestation failed: {exc}")
        return 1

    print("checkpoint attestation succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
