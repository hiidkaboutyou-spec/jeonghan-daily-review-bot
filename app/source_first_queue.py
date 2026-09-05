from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


def _handle(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


@dataclass(frozen=True, slots=True)
class SourceQueueEntry:
    source: str
    source_order: int
    state: str
    total_items: int
    done_items: int
    pending_items: int
    completeness_status: str
    completeness_error: str
    retry_count: int
    last_retry_status: str


@dataclass(frozen=True, slots=True)
class SourceQueueSnapshot:
    session_id: str
    total_sources: int
    active_source: str
    active_position: int
    current_draft_id: str
    current_item_number: int
    current_item_total: int
    sources: tuple[SourceQueueEntry, ...]

    @property
    def deferred_sources(self) -> tuple[str, ...]:
        return tuple(item.source for item in self.sources if item.state == "deferred")


class SourceFirstQueueStore:
    """Durable source-first navigation over the existing review inbox.

    `review_inbox.status` remains authoritative for each draft. This store only
    persists the active source, stable source membership/order, defer state and
    retry metadata, so Phase 5 cannot rewrite delivery or editorial truth.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_review_sessions (
                session_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                active_source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS source_review_one_active_session
                ON source_review_sessions(status) WHERE status='active';

            CREATE TABLE IF NOT EXISTS source_review_sources (
                session_id TEXT NOT NULL,
                source TEXT NOT NULL,
                source_order INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                deferred_at TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_status TEXT NOT NULL DEFAULT '',
                last_retry_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(session_id, source)
            );
            CREATE INDEX IF NOT EXISTS source_review_sources_order_idx
                ON source_review_sources(session_id, source_order, source);

            CREATE TABLE IF NOT EXISTS source_review_items (
                session_id TEXT NOT NULL,
                draft_id TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(session_id, draft_id)
            );
            CREATE INDEX IF NOT EXISTS source_review_items_source_idx
                ON source_review_items(session_id, source, created_at, draft_id);
            """
        )
        self.conn.commit()

    def sync(self, sources: Iterable[dict[str, Any]]) -> str:
        """Merge newly pending drafts into one restart-safe active review round."""
        configured = self._configured_sources(sources)
        pending_rows = self._pending_inbox_rows()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            session = self.conn.execute(
                "SELECT * FROM source_review_sessions WHERE status='active' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if session is None:
                if not pending_rows:
                    return ""
                session_id = uuid.uuid4().hex
                self.conn.execute(
                    "INSERT INTO source_review_sessions(session_id,status) VALUES(?,'active')",
                    (session_id,),
                )
            else:
                session_id = str(session["session_id"])

            known_orders = {name: order for name, order in configured}
            unknown = sorted(
                {
                    _handle(row["source"])
                    for row in pending_rows
                    if _handle(row["source"]) and _handle(row["source"]) not in known_orders
                }
            )
            for offset, source in enumerate(unknown, start=len(configured)):
                known_orders[source] = offset

            for source, order in configured:
                self._ensure_source(session_id, source, order)
            for source in unknown:
                self._ensure_source(session_id, source, known_orders[source])

            for row in pending_rows:
                source = _handle(row["source"]) or "unknown"
                if source not in known_orders:
                    known_orders[source] = len(known_orders)
                    self._ensure_source(session_id, source, known_orders[source])
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO source_review_items(session_id,draft_id,source,created_at)
                    VALUES(?,?,?,?)
                    """,
                    (session_id, str(row["draft_id"]), source, str(row["created_at"] or "")),
                )

            self._reconcile_states(session_id)
            self._activate_next(session_id)
            self.conn.execute(
                "UPDATE source_review_sessions SET updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                (session_id,),
            )
            return session_id

    def snapshot(self, session_id: str = "") -> SourceQueueSnapshot:
        if not session_id:
            row = self.conn.execute(
                "SELECT session_id FROM source_review_sessions WHERE status='active' "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            session_id = str(row["session_id"]) if row else ""
        if not session_id:
            return SourceQueueSnapshot("", 0, "", 0, "", 0, 0, ())

        session = self.conn.execute(
            "SELECT * FROM source_review_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if session is None:
            return SourceQueueSnapshot("", 0, "", 0, "", 0, 0, ())

        rows = self.conn.execute(
            "SELECT * FROM source_review_sources WHERE session_id=? ORDER BY source_order,source",
            (session_id,),
        ).fetchall()
        entries: list[SourceQueueEntry] = []
        for row in rows:
            total, pending = self._counts(session_id, str(row["source"]))
            proof_status, proof_error = self._latest_completeness(str(row["source"]))
            entries.append(
                SourceQueueEntry(
                    source=str(row["source"]),
                    source_order=int(row["source_order"]),
                    state=str(row["state"]),
                    total_items=total,
                    done_items=max(0, total - pending),
                    pending_items=pending,
                    completeness_status=proof_status,
                    completeness_error=proof_error,
                    retry_count=int(row["retry_count"]),
                    last_retry_status=str(row["last_retry_status"]),
                )
            )

        active_source = _handle(session["active_source"])
        active_position = 0
        current_draft_id = ""
        current_number = 0
        current_total = 0
        if active_source:
            for index, entry in enumerate(entries, start=1):
                if entry.source == active_source:
                    active_position = index
                    current_total = entry.total_items
                    current_number = (
                        min(entry.total_items, entry.done_items + 1)
                        if entry.pending_items
                        else entry.total_items
                    )
                    break
            item = self.conn.execute(
                """
                SELECT q.draft_id
                FROM source_review_items AS q
                JOIN review_inbox AS r ON r.draft_id=q.draft_id
                WHERE q.session_id=? AND q.source=? AND r.status='pending'
                ORDER BY q.created_at ASC, q.draft_id ASC
                LIMIT 1
                """,
                (session_id, active_source),
            ).fetchone()
            if item is not None:
                current_draft_id = str(item["draft_id"])

        return SourceQueueSnapshot(
            session_id=session_id,
            total_sources=len(entries),
            active_source=active_source,
            active_position=active_position,
            current_draft_id=current_draft_id,
            current_item_number=current_number,
            current_item_total=current_total,
            sources=tuple(entries),
        )

    def defer(self, session_id: str, source: str) -> bool:
        source = _handle(source)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT state FROM source_review_sources WHERE session_id=? AND source=?",
                (session_id, source),
            ).fetchone()
            if row is None:
                return False
            _, pending = self._counts(session_id, source)
            if pending <= 0:
                return False
            self.conn.execute(
                """
                UPDATE source_review_sources
                SET state='deferred',deferred_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND source=?
                """,
                (session_id, source),
            )
            self.conn.execute(
                """
                UPDATE source_review_sessions
                SET active_source=CASE WHEN active_source=? THEN '' ELSE active_source END,
                    updated_at=CURRENT_TIMESTAMP
                WHERE session_id=?
                """,
                (source, session_id),
            )
            self._activate_next(session_id)
        return True

    def resume(self, session_id: str, source: str) -> bool:
        source = _handle(source)
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT state FROM source_review_sources WHERE session_id=? AND source=?",
                (session_id, source),
            ).fetchone()
            if row is None or str(row["state"]) != "deferred":
                return False
            _, pending = self._counts(session_id, source)
            state = "pending" if pending else "complete"
            self.conn.execute(
                """
                UPDATE source_review_sources
                SET state=?,deferred_at='',updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND source=?
                """,
                (state, session_id, source),
            )
            # Resume never steals focus from another active source. It rejoins
            # configured order for the next source transition.
            self._activate_next(session_id)
        return True

    def begin_retry(self, session_id: str, source: str) -> bool:
        source = _handle(source)
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE source_review_sources
                SET retry_count=retry_count+1,last_retry_status='running',
                    last_retry_error='',updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND source=?
                """,
                (session_id, source),
            )
        return cur.rowcount > 0

    def finish_retry(self, session_id: str, source: str, *, success: bool, error: str = "") -> bool:
        source = _handle(source)
        status = "success" if success else "failed"
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE source_review_sources
                SET last_retry_status=?,last_retry_error=?,updated_at=CURRENT_TIMESTAMP
                WHERE session_id=? AND source=?
                """,
                (status, str(error or "")[:160], session_id, source),
            )
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()

    def _configured_sources(self, sources: Iterable[dict[str, Any]]) -> list[tuple[str, int]]:
        result: list[tuple[str, int]] = []
        seen: set[str] = set()
        for index, raw in enumerate(sources):
            if not raw.get("enabled", True):
                continue
            source = _handle(raw.get("handle"))
            if not source or source in seen:
                continue
            seen.add(source)
            # File order is editorial authority. Retrieval priority metadata must
            # not silently reshuffle the user's review lane.
            result.append((source, index))
        return result

    def _pending_inbox_rows(self) -> list[sqlite3.Row]:
        try:
            return self.conn.execute(
                """
                SELECT draft_id,source,created_at
                FROM review_inbox
                WHERE status='pending'
                ORDER BY created_at ASC,draft_id ASC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def _ensure_source(self, session_id: str, source: str, source_order: int) -> None:
        self.conn.execute(
            """
            INSERT INTO source_review_sources(session_id,source,source_order,state)
            VALUES(?,?,?,'pending')
            ON CONFLICT(session_id,source) DO UPDATE SET source_order=excluded.source_order
            """,
            (session_id, source, int(source_order)),
        )

    def _counts(self, session_id: str, source: str) -> tuple[int, int]:
        row = self.conn.execute(
            """
            SELECT count(*) AS total,
                   sum(CASE WHEN r.status='pending' THEN 1 ELSE 0 END) AS pending
            FROM source_review_items AS q
            LEFT JOIN review_inbox AS r ON r.draft_id=q.draft_id
            WHERE q.session_id=? AND q.source=?
            """,
            (session_id, source),
        ).fetchone()
        return int(row["total"] or 0), int(row["pending"] or 0)

    def _reconcile_states(self, session_id: str) -> None:
        rows = self.conn.execute(
            "SELECT source,state FROM source_review_sources WHERE session_id=?",
            (session_id,),
        ).fetchall()
        for row in rows:
            source, state = str(row["source"]), str(row["state"])
            _, pending = self._counts(session_id, source)
            if pending <= 0:
                next_state = "complete"
            elif state == "deferred":
                next_state = "deferred"
            elif state == "active":
                next_state = "active"
            else:
                next_state = "pending"
            if next_state != state:
                self.conn.execute(
                    """
                    UPDATE source_review_sources
                    SET state=?,updated_at=CURRENT_TIMESTAMP
                    WHERE session_id=? AND source=?
                    """,
                    (next_state, session_id, source),
                )

        session = self.conn.execute(
            "SELECT active_source FROM source_review_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        active = _handle(session["active_source"]) if session else ""
        if active:
            _, pending = self._counts(session_id, active)
            state = self.conn.execute(
                "SELECT state FROM source_review_sources WHERE session_id=? AND source=?",
                (session_id, active),
            ).fetchone()
            if pending <= 0 or state is None or str(state["state"]) != "active":
                self.conn.execute(
                    "UPDATE source_review_sessions SET active_source='' WHERE session_id=?",
                    (session_id,),
                )

    def _activate_next(self, session_id: str) -> None:
        session = self.conn.execute(
            "SELECT active_source FROM source_review_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if session is None or _handle(session["active_source"]):
            return
        rows = self.conn.execute(
            "SELECT source,state FROM source_review_sources "
            "WHERE session_id=? ORDER BY source_order,source",
            (session_id,),
        ).fetchall()
        for row in rows:
            source, state = str(row["source"]), str(row["state"])
            _, pending = self._counts(session_id, source)
            if pending > 0 and state != "deferred":
                self.conn.execute(
                    "UPDATE source_review_sources SET state='active',updated_at=CURRENT_TIMESTAMP "
                    "WHERE session_id=? AND source=?",
                    (session_id, source),
                )
                self.conn.execute(
                    "UPDATE source_review_sessions SET active_source=?,updated_at=CURRENT_TIMESTAMP "
                    "WHERE session_id=?",
                    (source, session_id),
                )
                return

        unresolved = self.conn.execute(
            """
            SELECT count(*) FROM source_review_sources AS s
            WHERE s.session_id=? AND s.state='deferred'
              AND EXISTS (
                SELECT 1 FROM source_review_items AS q
                JOIN review_inbox AS r ON r.draft_id=q.draft_id
                WHERE q.session_id=s.session_id AND q.source=s.source AND r.status='pending'
              )
            """,
            (session_id,),
        ).fetchone()[0]
        if not int(unresolved or 0):
            self.conn.execute(
                "UPDATE source_review_sessions SET status='complete',updated_at=CURRENT_TIMESTAMP "
                "WHERE session_id=?",
                (session_id,),
            )

    def _latest_completeness(self, source: str) -> tuple[str, str]:
        try:
            row = self.conn.execute(
                """
                SELECT status,error_class
                FROM completeness_attempts
                WHERE source=? AND finalized=1
                ORDER BY sequence DESC LIMIT 1
                """,
                (_handle(source),),
            ).fetchone()
        except sqlite3.OperationalError:
            return "unknown", ""
        if row is None:
            return "unknown", ""
        return str(row["status"] or "unknown"), str(row["error_class"] or "")[:160]
