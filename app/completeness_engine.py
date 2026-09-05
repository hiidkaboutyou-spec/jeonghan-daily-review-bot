"""Shadow completeness on the existing source-ledger SQLite connection."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any

from .completeness_evidence import TraversalEvidence
from .source_ledger import SourceLedgerStore

EVIDENCE_VERSION = 1
CORE_CONTRACT_VERSION = 1


def utc(value) -> str:
    dt = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    )
    if dt.tzinfo is None:
        raise ValueError("Completeness windows require timezone-aware timestamps")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def core_request(op: str, **fields):
    binary = os.environ.get("EDITORIAL_CORE_BINARY", "jeonghan-editorial-core")
    process = subprocess.run(
        [
            binary,
        ],
        input=json.dumps(
            {"contract_version": CORE_CONTRACT_VERSION, "op": op, **fields}
        )
        + "\n",
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
    )
    reply = json.loads(process.stdout)
    if (
        reply.get("contract_version") != CORE_CONTRACT_VERSION
        or reply.get("ok") is not True
    ):
        raise ValueError("Invalid editorial core response")
    return reply["result"]


def proof_inputs(
    evidence: TraversalEvidence, error_class: str = ""
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert raw traversal facts into the conservative Rust proof contract."""
    expected = {
        str(value) for value in evidence.expected_window_ids if str(value)
    }
    observed = {str(value) for value in evidence.observation_ids if str(value)}
    missing = expected - observed
    coverage_complete = not missing
    ordered_boundary = bool(
        evidence.lower_boundary_proven
        and evidence.timeline_order_valid
        and coverage_complete
    )
    terminal_proven = bool(
        evidence.timeline_order_valid
        and coverage_complete
        and (evidence.exhausted or ordered_boundary)
    )
    unresolved_boundary = bool(evidence.lower_boundary and not ordered_boundary)
    proof = {
        "pages": max(0, int(evidence.pages)),
        "raw_count": max(0, int(evidence.raw_count)),
        "valid_response": bool(evidence.valid_response),
        "exhausted": terminal_proven,
        "resumed": bool(evidence.resumed),
        "lower_boundary": unresolved_boundary,
        "failed": bool(error_class),
    }
    detail = {
        "provider_exhausted": bool(evidence.exhausted),
        "lower_boundary_observed": bool(evidence.lower_boundary),
        "lower_boundary_proven": bool(evidence.lower_boundary_proven),
        "timeline_order_valid": bool(evidence.timeline_order_valid),
        "expected_window_ids": sorted(expected),
        "expected_window_count": len(expected),
        "observed_expected_count": len(expected & observed),
        "missing_expected_ids": sorted(missing),
        "expected_coverage_complete": coverage_complete,
        "terminal_proven": terminal_proven,
        "newest_top_level_at": str(evidence.newest_top_level_at or ""),
        "oldest_top_level_at": str(evidence.oldest_top_level_at or ""),
    }
    return proof, detail


def _verdict_metadata(
    *,
    status: str,
    evidence: TraversalEvidence,
    detail: dict[str, Any],
    error_class: str,
) -> tuple[str, str]:
    if status == "complete" and detail["provider_exhausted"]:
        return "validated_provider_exhaustion", "provider_pagination_exhausted"
    if status == "complete" and detail["lower_boundary_proven"]:
        return "validated_ordered_lower_boundary", "window_lower_boundary_crossed"

    if error_class == "EditorialCoreUnavailable":
        return "unproven_core_unavailable", "deterministic_core_unavailable"

    if status == "partial":
        if error_class:
            return "partial_provider_failure", f"provider_failure:{error_class}"[:160]
        if evidence.resumed:
            return "partial_resumed_checkpoint", "resume_continuity_not_proven"
        if detail["missing_expected_ids"]:
            return "partial_observation_coverage_gap", "expected_observation_missing"
        if not detail["timeline_order_valid"]:
            return "partial_timeline_order_invalid", "timeline_order_not_proven"
        if not evidence.valid_response:
            return "partial_invalid_provider_response", "provider_response_not_proof_eligible"
        return "partial_unproven_termination", "terminal_condition_not_proven"

    if error_class:
        return "unproven_provider_failure", f"provider_failure:{error_class}"[:160]
    if evidence.resumed:
        return "unproven_resumed_checkpoint", "resume_continuity_not_proven"
    if evidence.pages <= 0:
        return "unproven_no_validated_page", "no_validated_provider_page"
    if not evidence.valid_response:
        return "unproven_invalid_provider_response", "provider_response_not_proof_eligible"
    if not detail["timeline_order_valid"]:
        return "unproven_timeline_order_invalid", "timeline_order_not_proven"
    if detail["missing_expected_ids"]:
        return "unproven_observation_coverage_gap", "expected_observation_missing"
    return "unproven_ambiguous_termination", "terminal_condition_not_proven"


class CompletenessEngine:
    def __init__(self, ledger: SourceLedgerStore):
        self.conn = ledger.conn
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS completeness_attempts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                source TEXT NOT NULL,
                source_order INTEGER NOT NULL,
                window_start TEXT NOT NULL,
                window_end TEXT NOT NULL,
                status TEXT NOT NULL,
                attempted INTEGER NOT NULL DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                evidence TEXT NOT NULL DEFAULT '{}',
                error_class TEXT NOT NULL DEFAULT 'NotAttempted',
                retained_count INTEGER NOT NULL DEFAULT 0,
                legacy_status TEXT NOT NULL DEFAULT '',
                finalized INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                attempt_number INTEGER NOT NULL DEFAULT 0,
                cursor_before TEXT NOT NULL DEFAULT '',
                cursor_candidate TEXT NOT NULL DEFAULT '',
                cursor_after TEXT NOT NULL DEFAULT '',
                cursor_advanced INTEGER NOT NULL DEFAULT 0,
                evidence_version INTEGER NOT NULL DEFAULT 1,
                UNIQUE(run_id, source)
            );
            CREATE TABLE IF NOT EXISTS completeness_shadow_cursors (
                source TEXT PRIMARY KEY,
                complete_through TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                sequence INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS completeness_observations (
                attempt_id TEXT NOT NULL,
                post_id TEXT NOT NULL,
                PRIMARY KEY(attempt_id,post_id)
            );
            """
        )
        self._ensure_attempt_columns()

    def _ensure_attempt_columns(self) -> None:
        """Additive migration for databases created by the first Phase 4 shadow."""
        existing = {
            str(row["name"])
            for row in self.conn.execute(
                "PRAGMA table_info(completeness_attempts)"
            ).fetchall()
        }
        additions = {
            "started_at": "TEXT NOT NULL DEFAULT ''",
            "finished_at": "TEXT NOT NULL DEFAULT ''",
            "attempt_number": "INTEGER NOT NULL DEFAULT 0",
            "cursor_before": "TEXT NOT NULL DEFAULT ''",
            "cursor_candidate": "TEXT NOT NULL DEFAULT ''",
            "cursor_after": "TEXT NOT NULL DEFAULT ''",
            "cursor_advanced": "INTEGER NOT NULL DEFAULT 0",
            "evidence_version": "INTEGER NOT NULL DEFAULT 1",
        }
        changed = False
        for column, definition in additions.items():
            if column in existing:
                continue
            self.conn.execute(
                f"ALTER TABLE completeness_attempts ADD COLUMN {column} {definition}"
            )
            changed = True
        if changed:
            self.conn.commit()

    @staticmethod
    def _load_evidence(value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def checkpoint(self, attempt_id, evidence):
        payload = {
            "evidence_version": EVIDENCE_VERSION,
            "core_contract_version": CORE_CONTRACT_VERSION,
            "pages": max(0, int(evidence.pages)),
            "pages_completed": max(0, int(evidence.pages)),
            "raw_count": max(0, int(evidence.raw_count)),
            "provider_cursor": str(evidence.provider_cursor or "")[:4096],
            "valid_response": bool(evidence.valid_response),
            "expected_window_ids": sorted(
                str(value) for value in evidence.expected_window_ids
            )[:5000],
            "lower_boundary_proven": bool(evidence.lower_boundary_proven),
            "timeline_order_valid": bool(evidence.timeline_order_valid),
            "newest_top_level_at": str(evidence.newest_top_level_at or ""),
            "oldest_top_level_at": str(evidence.oldest_top_level_at or ""),
        }
        with self.conn:
            self.conn.execute(
                """
                UPDATE completeness_attempts SET evidence=?
                WHERE attempt_id=? AND finalized=0
                """,
                (json.dumps(payload, sort_keys=True), attempt_id),
            )

    def link_observation(self, attempt_id, post_id):
        with self.conn:
            self.conn.execute(
                """INSERT OR IGNORE INTO completeness_observations
                SELECT attempt_id,? FROM completeness_attempts
                WHERE attempt_id=? AND finalized=0""",
                (post_id, attempt_id),
            )

    def plan(self, sources, start, end) -> str:
        start, end = utc(start), utc(end)
        # Both values are normalized to the same fixed-width UTC representation.
        if start >= end:
            raise ValueError("Completeness window must be nonempty")
        run_id = uuid.uuid4().hex
        with self.conn:
            for index, source in enumerate(sources):
                if not source.get("enabled", True):
                    continue
                handle = (
                    str(source.get("handle", ""))
                    .strip()
                    .lstrip("@")
                    .casefold()
                )
                handle = handle or f"invalid-source-{index}"
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO completeness_attempts
                    (attempt_id,run_id,source,source_order,window_start,window_end,
                     status,evidence_version)
                    VALUES(?,?,?,?,?,?,'unproven',?)
                    """,
                    (
                        uuid.uuid4().hex,
                        run_id,
                        handle,
                        index,
                        start,
                        end,
                        EVIDENCE_VERSION,
                    ),
                )
        return run_id

    def start(self, run_id, source) -> str:
        source = source.strip().lstrip("@").casefold()
        now = _utc_now()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """
                SELECT * FROM completeness_attempts
                WHERE run_id=? AND source=?
                """,
                (run_id, source),
            ).fetchone()
            if row is None or row["finalized"] or row["attempted"]:
                raise ValueError("Source attempt is missing or already started")
            retries = self.conn.execute(
                """SELECT count(*) FROM completeness_attempts
                WHERE source=? AND window_start=? AND window_end=? AND attempted=1""",
                (source, row["window_start"], row["window_end"]),
            ).fetchone()[0]
            cursor = self.conn.execute(
                "SELECT complete_through FROM completeness_shadow_cursors WHERE source=?",
                (source,),
            ).fetchone()
            cursor_before = str(cursor["complete_through"]) if cursor else ""
            self.conn.execute(
                """UPDATE completeness_attempts SET attempted=1,
                status='attempting',error_class='',retry_count=?,attempt_number=?,
                started_at=?,cursor_before=?,cursor_candidate=?,cursor_after='',
                cursor_advanced=0,evidence_version=?
                WHERE attempt_id=?""",
                (
                    retries,
                    retries + 1,
                    now,
                    cursor_before,
                    row["window_end"],
                    EVIDENCE_VERSION,
                    row["attempt_id"],
                ),
            )
        return row["attempt_id"]

    def finish(
        self,
        attempt_id: str,
        evidence: TraversalEvidence,
        retained: int,
        error_class: str = "",
    ):
        proof, proof_detail = proof_inputs(evidence, error_class)
        try:
            status = core_request("evaluate_completeness", proof=proof)
            if status not in ("complete", "partial", "unproven"):
                raise ValueError("Invalid completeness state")
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            status, error_class = "unproven", "EditorialCoreUnavailable"

        proof_kind, termination_reason = _verdict_metadata(
            status=status,
            evidence=evidence,
            detail=proof_detail,
            error_class=error_class,
        )
        payload = {
            "evidence_version": EVIDENCE_VERSION,
            "core_contract_version": CORE_CONTRACT_VERSION,
            **proof,
            **proof_detail,
            "pages_completed": max(0, int(evidence.pages)),
            "provider_cursor": str(evidence.provider_cursor or "")[:4096],
            "raw_observation_count": len(evidence.observation_ids),
            "observation_ids": sorted(evidence.observation_ids),
            "proof_kind": proof_kind,
            "termination_reason": termination_reason,
        }

        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT * FROM completeness_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None or not row["attempted"]:
                raise ValueError("Unknown attempt")
            if row["finalized"]:
                return

            cursor_advanced = False
            if status == "complete":
                cursor = self.conn.execute(
                    """
                    SELECT * FROM completeness_shadow_cursors WHERE source=?
                    """,
                    (row["source"],),
                ).fetchone()
                # Values in this table originate only from normalized plan bounds /
                # Rust-validated candidates, so fixed-width UTC string ordering is
                # equivalent to instant ordering here. Rust remains the authority.
                eligible = cursor is None or (
                    row["window_end"] > cursor["complete_through"]
                    and row["sequence"] > cursor["sequence"]
                )
                contiguous = (
                    cursor is None
                    or row["window_start"] <= cursor["complete_through"]
                )
                if eligible and contiguous:
                    try:
                        through = core_request(
                            "advance_cursor",
                            state={
                                "source_handle": row["source"],
                                "window_start": row["window_start"],
                                "window_end": row["window_end"],
                                "completeness": status,
                                "complete_through": (
                                    cursor["complete_through"] if cursor else None
                                ),
                            },
                            candidate=row["window_end"],
                        )
                        if through != row["window_end"]:
                            raise ValueError("Invalid core cursor")
                    except (
                        OSError,
                        ValueError,
                        KeyError,
                        subprocess.SubprocessError,
                    ):
                        status, error_class = (
                            "unproven",
                            "EditorialCoreUnavailable",
                        )
                    else:
                        self.conn.execute(
                            """INSERT INTO completeness_shadow_cursors
                            VALUES(?,?,?,?)
                            ON CONFLICT(source) DO UPDATE SET
                            complete_through=excluded.complete_through,
                            attempt_id=excluded.attempt_id,
                            sequence=excluded.sequence""",
                            (
                                row["source"],
                                through,
                                attempt_id,
                                row["sequence"],
                            ),
                        )
                        cursor_advanced = True
                elif not contiguous:
                    payload["cursor_gap"] = True

            if status != "complete":
                proof_kind, termination_reason = _verdict_metadata(
                    status=status,
                    evidence=evidence,
                    detail=proof_detail,
                    error_class=error_class,
                )
                payload["proof_kind"] = proof_kind
                payload["termination_reason"] = termination_reason

            cursor_after_row = self.conn.execute(
                """
                SELECT complete_through FROM completeness_shadow_cursors
                WHERE source=?
                """,
                (row["source"],),
            ).fetchone()
            cursor_after = (
                str(cursor_after_row["complete_through"])
                if cursor_after_row
                else str(row["cursor_before"] or "")
            )
            # Equal/stale COMPLETE results may stay COMPLETE while leaving the
            # durable cursor untouched. "advanced" means this attempt moved it.
            if cursor_after == str(row["cursor_before"] or ""):
                cursor_advanced = False

            payload["error_summary"] = str(error_class or "")[:160]
            payload["cursor_before"] = str(row["cursor_before"] or "")
            payload["cursor_candidate"] = str(row["window_end"])
            payload["cursor_after"] = cursor_after
            payload["cursor_advanced"] = bool(cursor_advanced)

            self.conn.execute(
                """UPDATE completeness_attempts SET status=?,evidence=?,
                error_class=?,retained_count=?,finalized=1,finished_at=?,
                cursor_candidate=?,cursor_after=?,cursor_advanced=?,
                legacy_status=COALESCE(
                    (SELECT last_status FROM source_cursors WHERE source=?),''
                )
                WHERE attempt_id=?""",
                (
                    status,
                    json.dumps(payload, sort_keys=True),
                    str(error_class or "")[:160],
                    max(0, retained),
                    _utc_now(),
                    row["window_end"],
                    cursor_after,
                    int(cursor_advanced),
                    row["source"],
                    attempt_id,
                ),
            )

    def close_run(self, run_id: str, reason: str = "Interrupted"):
        now = _utc_now()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            rows = self.conn.execute(
                """
                SELECT * FROM completeness_attempts
                WHERE run_id=? AND finalized=0
                """,
                (run_id,),
            ).fetchall()
            for row in rows:
                error_class = reason if row["attempted"] else "NotAttempted"
                cursor_before = str(row["cursor_before"] or "")
                if not row["attempted"]:
                    cursor_row = self.conn.execute(
                        """SELECT complete_through FROM completeness_shadow_cursors
                        WHERE source=?""",
                        (row["source"],),
                    ).fetchone()
                    cursor_before = (
                        str(cursor_row["complete_through"]) if cursor_row else ""
                    )
                payload = self._load_evidence(row["evidence"])
                payload.update(
                    {
                        "evidence_version": EVIDENCE_VERSION,
                        "core_contract_version": CORE_CONTRACT_VERSION,
                        "proof_kind": (
                            "unproven_interrupted"
                            if row["attempted"]
                            else "unproven_not_attempted"
                        ),
                        "termination_reason": (
                            "attempt_interrupted"
                            if row["attempted"]
                            else "source_not_attempted"
                        ),
                        "error_summary": str(error_class)[:160],
                        "cursor_before": cursor_before,
                        "cursor_candidate": str(row["window_end"]),
                        "cursor_after": cursor_before,
                        "cursor_advanced": False,
                    }
                )
                self.conn.execute(
                    """UPDATE completeness_attempts SET status='unproven',
                    error_class=?,evidence=?,finalized=1,finished_at=?,
                    cursor_candidate=?,cursor_after=?,cursor_advanced=0
                    WHERE attempt_id=?""",
                    (
                        error_class,
                        json.dumps(payload, sort_keys=True),
                        now,
                        row["window_end"],
                        cursor_before,
                        row["attempt_id"],
                    ),
                )

    def report(self, run_id: str) -> dict[str, Any]:
        rows = [
            dict(row)
            for row in self.conn.execute(
                """
                SELECT * FROM completeness_attempts
                WHERE run_id=? ORDER BY source_order,source
                """,
                (run_id,),
            )
        ]
        for row in rows:
            row["evidence"] = self._load_evidence(row["evidence"])
            linked = [
                link[0]
                for link in self.conn.execute(
                    """SELECT post_id FROM completeness_observations
                    WHERE attempt_id=? ORDER BY post_id""",
                    (row["attempt_id"],),
                )
            ]
            if linked:
                row["evidence"]["observation_ids"] = linked
                row["evidence"]["raw_observation_count"] = len(linked)
            row["evidence"].setdefault("evidence_version", row["evidence_version"])
            row["evidence"].setdefault("cursor_before", row["cursor_before"])
            row["evidence"].setdefault(
                "cursor_candidate", row["cursor_candidate"]
            )
            row["evidence"].setdefault("cursor_after", row["cursor_after"])
            row["evidence"].setdefault(
                "cursor_advanced", bool(row["cursor_advanced"])
            )
        return {
            "mode": "shadow",
            "run_id": run_id,
            "configured": len(rows),
            "attempted": sum(row["attempted"] for row in rows),
            "complete": sum(row["status"] == "complete" for row in rows),
            "healthy": bool(rows)
            and all(
                row["status"] == "complete" and row["finalized"] for row in rows
            ),
            "sources": rows,
        }
