from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database_checkpoint_attestation import attest_database_checkpoint, main
from app.production_outcome import (
    OutcomeBuilder,
    OutcomeStatus,
    classify_outcome,
    load_outcome,
    save_outcome,
)


class _Cursor:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, *, quick_rows=None, checkpoint_row=(0, 0, 0)):
        self.quick_rows = [("ok",)] if quick_rows is None else quick_rows
        self.checkpoint_row = checkpoint_row
        self.closed = False

    def execute(self, statement):
        if statement == "PRAGMA quick_check":
            return _Cursor(rows=self.quick_rows)
        if statement == "PRAGMA wal_checkpoint(TRUNCATE)":
            return _Cursor(row=self.checkpoint_row)
        raise AssertionError(statement)

    def close(self):
        self.closed = True


class DatabaseCheckpointAttestationTests(unittest.TestCase):
    def _write_outcome(self, path: Path) -> None:
        builder = OutcomeBuilder(run_id="checkpoint-test")
        builder.mark_state_checkpoint(True)
        save_outcome(builder.finalize(), path)

    def test_successful_checkpoint_attests_attempt_and_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "private-review.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO item(value) VALUES ('ok')")

            outcome_path = root / "production-outcome.json"
            self._write_outcome(outcome_path)

            attest_database_checkpoint(database, outcome_path)

            outcome = load_outcome(outcome_path)
            self.assertTrue(outcome.state.database_checkpoint_attempted)
            self.assertTrue(outcome.state.database_checkpoint_success)
            self.assertNotIn("database_checkpoint_failed", outcome.outcome_reasons)

    def test_missing_database_persists_failed_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outcome_path = root / "production-outcome.json"
            self._write_outcome(outcome_path)

            with self.assertRaisesRegex(RuntimeError, "database file is missing"):
                attest_database_checkpoint(root / "missing.sqlite3", outcome_path)

            outcome = load_outcome(outcome_path)
            self.assertTrue(outcome.state.database_checkpoint_attempted)
            self.assertFalse(outcome.state.database_checkpoint_success)
            self.assertEqual(outcome.outcome_status, OutcomeStatus.FAILED.value)
            self.assertEqual(outcome.outcome_reasons, ["database_checkpoint_failed"])

    def test_quick_check_failure_is_fail_closed_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database = root / "private-review.sqlite3"
            database.touch()
            outcome_path = root / "production-outcome.json"
            self._write_outcome(outcome_path)
            connection = _Connection(quick_rows=[("corrupt",)])

            with patch(
                "app.database_checkpoint_attestation.sqlite3.connect",
                return_value=connection,
            ):
                with self.assertRaisesRegex(RuntimeError, "quick_check failed"):
                    attest_database_checkpoint(database, outcome_path)

            outcome = load_outcome(outcome_path)
            self.assertTrue(connection.closed)
            self.assertTrue(outcome.state.database_checkpoint_attempted)
            self.assertFalse(outcome.state.database_checkpoint_success)
            self.assertEqual(outcome.outcome_reasons, ["database_checkpoint_failed"])

    def test_busy_or_partial_checkpoint_is_fail_closed(self):
        for checkpoint_row in ((1, 5, 4), (0, 5, 4)):
            with self.subTest(checkpoint_row=checkpoint_row):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    database = root / "private-review.sqlite3"
                    database.touch()
                    outcome_path = root / "production-outcome.json"
                    self._write_outcome(outcome_path)
                    connection = _Connection(checkpoint_row=checkpoint_row)

                    with patch(
                        "app.database_checkpoint_attestation.sqlite3.connect",
                        return_value=connection,
                    ):
                        with self.assertRaisesRegex(RuntimeError, "busy or partial"):
                            attest_database_checkpoint(database, outcome_path)

                    outcome = load_outcome(outcome_path)
                    self.assertTrue(outcome.state.database_checkpoint_attempted)
                    self.assertFalse(outcome.state.database_checkpoint_success)
                    self.assertEqual(outcome.outcome_status, OutcomeStatus.FAILED.value)

    def test_legacy_outcome_without_attempted_field_stays_backward_compatible(self):
        with tempfile.TemporaryDirectory() as temp:
            outcome_path = Path(temp) / "production-outcome.json"
            self._write_outcome(outcome_path)
            payload = json.loads(outcome_path.read_text(encoding="utf-8"))
            payload["state"].pop("database_checkpoint_attempted", None)
            payload["state"]["database_checkpoint_success"] = False
            outcome_path.write_text(json.dumps(payload), encoding="utf-8")

            outcome = load_outcome(outcome_path)
            self.assertFalse(outcome.state.database_checkpoint_attempted)
            status, reasons = classify_outcome(outcome)
            self.assertNotIn("database_checkpoint_failed", reasons)
            self.assertNotEqual(status, OutcomeStatus.FAILED.value)

    def test_cli_error_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            outcome_path = Path(temp) / "production-outcome.json"
            self._write_outcome(outcome_path)
            argv = [
                "database_checkpoint_attestation",
                "--database",
                str(Path(temp) / "missing.sqlite3"),
                "--outcome",
                str(outcome_path),
            ]
            with patch.object(sys, "argv", argv):
                with patch("builtins.print") as output:
                    self.assertEqual(main(), 1)
            message = output.call_args.args[0]
            self.assertIn("database file is missing", message)
            self.assertNotIn(str(Path(temp)), message)

    def test_workflow_runs_attestation_before_outcome_upload(self):
        workflow = Path(".github/workflows/main.yml").read_text(encoding="utf-8")
        attest = "python -m app.database_checkpoint_attestation"
        upload = "- name: Upload production outcome artifact"
        self.assertIn(attest, workflow)
        self.assertLess(workflow.index(attest), workflow.index(upload))
        self.assertNotIn("conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')", workflow)


if __name__ == "__main__":
    unittest.main()
