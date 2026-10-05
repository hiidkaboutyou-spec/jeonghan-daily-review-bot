from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.database_checkpoint_attestation import attest_database_checkpoint
from app.production_outcome import (
    OutcomeBuilder,
    OutcomeStatus,
    load_outcome,
    save_outcome,
)


def _write_outcome(path: Path) -> None:
    builder = OutcomeBuilder(run_id="test")
    builder.mark_state_checkpoint(True)
    outcome = builder.finalize()
    save_outcome(outcome, path)


def test_successful_checkpoint_attests_complete(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO item(value) VALUES ('ok')")

    outcome_path = tmp_path / "production-outcome.json"
    _write_outcome(outcome_path)

    attest_database_checkpoint(db, outcome_path)

    outcome = load_outcome(outcome_path)
    assert outcome.state.database_checkpoint_attempted is True
    assert outcome.state.database_checkpoint_success is True
    assert "database_checkpoint_failed" not in outcome.outcome_reasons


def test_missing_database_persists_failed_attempt(tmp_path: Path) -> None:
    outcome_path = tmp_path / "production-outcome.json"
    _write_outcome(outcome_path)

    with pytest.raises(RuntimeError, match="database file is missing"):
        attest_database_checkpoint(tmp_path / "missing.db", outcome_path)

    outcome = load_outcome(outcome_path)
    assert outcome.state.database_checkpoint_attempted is True
    assert outcome.state.database_checkpoint_success is False
    assert outcome.outcome_status == OutcomeStatus.FAILED.value
    assert outcome.outcome_reasons == ["database_checkpoint_failed"]


def test_legacy_artifact_without_attempted_field_is_not_checkpoint_failure(tmp_path: Path) -> None:
    outcome_path = tmp_path / "production-outcome.json"
    _write_outcome(outcome_path)
    payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    payload["state"].pop("database_checkpoint_attempted", None)
    payload["state"]["database_checkpoint_success"] = False
    outcome_path.write_text(json.dumps(payload), encoding="utf-8")

    outcome = load_outcome(outcome_path)

    assert outcome.state.database_checkpoint_attempted is False
    assert "database_checkpoint_failed" not in outcome.outcome_reasons


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchall(self):
        return self._row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, checkpoint_row=(0, 0, 0), quick_rows=None):
        self.checkpoint_row = checkpoint_row
        self.quick_rows = [("ok",)] if quick_rows is None else quick_rows
        self.closed = False

    def execute(self, statement):
        if statement == "PRAGMA quick_check":
            return _Cursor(self.quick_rows)
        if statement == "PRAGMA wal_checkpoint(TRUNCATE)":
            return _Cursor(self.checkpoint_row)
        raise AssertionError(statement)

    def close(self):
        self.closed = True


@pytest.mark.parametrize("checkpoint_row", [(1, 5, 4), (0, 5, 4)])
def test_busy_or_partial_checkpoint_fails_closed(
    tmp_path: Path, checkpoint_row: tuple[int, int, int]
) -> None:
    db = tmp_path / "state.db"
    db.touch()
    outcome_path = tmp_path / "production-outcome.json"
    _write_outcome(outcome_path)
    connection = _Connection(checkpoint_row=checkpoint_row)

    with patch("app.database_checkpoint_attestation.sqlite3.connect", return_value=connection):
        with pytest.raises(RuntimeError, match="busy or partial"):
            attest_database_checkpoint(db, outcome_path)

    outcome = load_outcome(outcome_path)
    assert connection.closed is True
    assert outcome.state.database_checkpoint_attempted is True
    assert outcome.state.database_checkpoint_success is False
    assert outcome.outcome_status == OutcomeStatus.FAILED.value


def test_quick_check_failure_fails_closed_and_closes_connection(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    db.touch()
    outcome_path = tmp_path / "production-outcome.json"
    _write_outcome(outcome_path)
    connection = _Connection(quick_rows=[("corrupt",)])

    with patch("app.database_checkpoint_attestation.sqlite3.connect", return_value=connection):
        with pytest.raises(RuntimeError, match="quick_check failed"):
            attest_database_checkpoint(db, outcome_path)

    outcome = load_outcome(outcome_path)
    assert connection.closed is True
    assert outcome.state.database_checkpoint_attempted is True
    assert outcome.state.database_checkpoint_success is False
    assert outcome.outcome_reasons == ["database_checkpoint_failed"]
