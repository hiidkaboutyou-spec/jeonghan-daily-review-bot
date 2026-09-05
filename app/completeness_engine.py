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


def utc(value) -> str:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Completeness windows require timezone-aware timestamps")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def core_request(op: str, **fields):
    binary = os.environ.get("EDITORIAL_CORE_BINARY", "jeonghan-editorial-core")
    process = subprocess.run(
        [binary], input=json.dumps({"contract_version": 1, "op": op, **fields}) + "\n",
        text=True, capture_output=True, timeout=5, check=True,
    )
    reply = json.loads(process.stdout)
    if reply.get("contract_version") != 1 or reply.get("ok") is not True:
        raise ValueError("Invalid editorial core response")
    return reply["result"]


def proof_inputs(evidence: TraversalEvidence, error_class: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert raw traversal facts into the conservative Rust proof contract.

    The Rust v1 contract already understands a generic terminal bit. Python may set
    that bit from provider exhaustion OR a structurally proven lower boundary, but
    only after every top-level in-window ID exposed by those raw pages has reached
    the Phase 1 observation path.
    """
    expected = {str(value) for value in evidence.expected_window_ids if str(value)}
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
    }
    return proof, detail


class CompletenessEngine:
    def __init__(self, ledger: SourceLedgerStore):
        self.conn = ledger.conn
        self.conn.executescript("""
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
        """)

    def checkpoint(self, attempt_id, evidence):
        payload = {
            "pages": max(0, int(evidence.pages)),
            "raw_count": max(0, int(evidence.raw_count)),
            "provider_cursor": str(evidence.provider_cursor or "")[:4096],
            "valid_response": bool(evidence.valid_response),
            "expected_window_ids": sorted(str(value) for value in evidence.expected_window_ids)[:5000],
            "lower_boundary_proven": bool(evidence.lower_boundary_proven),
            "timeline_order_valid": bool(evidence.timeline_order_valid),
        }
        with self.conn:
            self.conn.execute(
                "UPDATE completeness_attempts SET evidence=? WHERE attempt_id=? AND finalized=0",
                (json.dumps(payload, sort_keys=True), attempt_id),
            )

    def link_observation(self, attempt_id, post_id):
        with self.conn:
            self.conn.execute("""INSERT OR IGNORE INTO completeness_observations
                SELECT attempt_id,? FROM completeness_attempts WHERE attempt_id=? AND finalized=0""", (post_id, attempt_id))

    def plan(self, sources, start, end) -> str:
        start, end = utc(start), utc(end)
        if start >= end:
            raise ValueError("Completeness window must be nonempty")
        run_id = uuid.uuid4().hex
        with self.conn:
            for index, source in enumerate(sources):
                if not source.get("enabled", True):
                    continue
                handle = str(source.get("handle", "")).strip().lstrip("@").casefold()
                # Invalid enabled configuration remains visible, never disappears.
                handle = handle or f"invalid-source-{index}"
                self.conn.execute("""
                    INSERT OR IGNORE INTO completeness_attempts
                    (attempt_id,run_id,source,source_order,window_start,window_end,status)
                    VALUES(?,?,?,?,?,?,'unproven')
                """, (uuid.uuid4().hex, run_id, handle, index, start, end))
        return run_id

    def start(self, run_id, source) -> str:
        source = source.strip().lstrip("@").casefold()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT * FROM completeness_attempts WHERE run_id=? AND source=?", (run_id, source)).fetchone()
            if row is None or row["finalized"] or row["attempted"]:
                raise ValueError("Source attempt is missing or already started")
            retries = self.conn.execute("""SELECT count(*) FROM completeness_attempts
                WHERE source=? AND window_start=? AND window_end=? AND attempted=1""",
                (source, row["window_start"], row["window_end"])).fetchone()[0]
            self.conn.execute("""UPDATE completeness_attempts SET attempted=1,
                status='attempting',error_class='',retry_count=? WHERE attempt_id=?""", (retries, row["attempt_id"]))
        return row["attempt_id"]

    def finish(self, attempt_id: str, evidence: TraversalEvidence, retained: int, error_class: str = ""):
        proof, proof_detail = proof_inputs(evidence, error_class)
        try:
            status = core_request("evaluate_completeness", proof=proof)
            if status not in ("complete", "partial", "unproven"):
                raise ValueError("Invalid completeness state")
        except (OSError, ValueError, KeyError, subprocess.SubprocessError):
            status, error_class = "unproven", "EditorialCoreUnavailable"

        if status == "complete" and proof_detail["provider_exhausted"]:
            proof_kind = "validated_provider_exhaustion"
        elif status == "complete" and proof_detail["lower_boundary_proven"]:
            proof_kind = "validated_ordered_lower_boundary"
        else:
            proof_kind = "bounded_window_unproven"
        payload = {
            **proof,
            **proof_detail,
            "provider_cursor": evidence.provider_cursor,
            "raw_observation_count": len(evidence.observation_ids),
            "observation_ids": sorted(evidence.observation_ids),
            "proof_kind": proof_kind,
        }

        # Serialize writers before reading the cursor; rollback covers both rows.
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT * FROM completeness_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            if row is None or not row["attempted"]:
                raise ValueError("Unknown attempt")
            if row["finalized"]:
                return  # Immutable completed attempts; duplicate results are idempotent.
            if status == "complete":
                cursor = self.conn.execute("SELECT * FROM completeness_shadow_cursors WHERE source=?", (row["source"],)).fetchone()
                # Older/equal proven metadata is never replaced by a stale result.
                eligible = cursor is None or (row["window_end"] > cursor["complete_through"] and row["sequence"] > cursor["sequence"])
                # A gap cannot be silently skipped by advancing a watermark.
                contiguous = cursor is None or row["window_start"] <= cursor["complete_through"]
                if eligible and contiguous:
                    try:
                        through = core_request("advance_cursor", state={
                            "source_handle": row["source"], "window_start": row["window_start"],
                            "window_end": row["window_end"], "completeness": status,
                            "complete_through": cursor["complete_through"] if cursor else None,
                        }, candidate=row["window_end"])
                        if through != row["window_end"]:
                            raise ValueError("Invalid core cursor")
                    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                        status, error_class = "unproven", "EditorialCoreUnavailable"
                    else:
                        self.conn.execute("""INSERT INTO completeness_shadow_cursors VALUES(?,?,?,?)
                            ON CONFLICT(source) DO UPDATE SET complete_through=excluded.complete_through,
                            attempt_id=excluded.attempt_id,sequence=excluded.sequence""",
                            (row["source"], through, attempt_id, row["sequence"]))
                elif not contiguous:
                    payload["cursor_gap"] = True
            if status != "complete":
                payload["proof_kind"] = "bounded_window_unproven"
            payload["error_summary"] = error_class[:160]
            self.conn.execute("""UPDATE completeness_attempts SET status=?,evidence=?,
                error_class=?,retained_count=?,finalized=1,
                legacy_status=COALESCE((SELECT last_status FROM source_cursors WHERE source=?),'')
                WHERE attempt_id=?""",
                (status, json.dumps(payload, sort_keys=True), error_class[:160], max(0, retained), row["source"], attempt_id))

    def close_run(self, run_id: str, reason: str = "Interrupted"):
        with self.conn:
            self.conn.execute("""UPDATE completeness_attempts SET status='unproven',
                error_class=CASE WHEN attempted=1 THEN ? ELSE 'NotAttempted' END,
                finalized=1 WHERE run_id=? AND finalized=0""", (reason, run_id))

    def report(self, run_id: str) -> dict[str, Any]:
        rows = [dict(row) for row in self.conn.execute(
            "SELECT * FROM completeness_attempts WHERE run_id=? ORDER BY source_order,source", (run_id,))]
        for row in rows:
            row["evidence"] = json.loads(row["evidence"])
            linked = [link[0] for link in self.conn.execute(
                "SELECT post_id FROM completeness_observations WHERE attempt_id=? ORDER BY post_id", (row["attempt_id"],))]
            if linked:
                row["evidence"]["observation_ids"] = linked
                row["evidence"]["raw_observation_count"] = len(linked)
        return {"mode": "shadow", "run_id": run_id, "configured": len(rows),
                "attempted": sum(row["attempted"] for row in rows),
                "complete": sum(row["status"] == "complete" for row in rows),
                "healthy": bool(rows) and all(row["status"] == "complete" and row["finalized"] for row in rows),
                "sources": rows}
