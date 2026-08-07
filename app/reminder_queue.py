from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ReminderJob:
    id: str
    draft_id: str
    due_at: str
    status: str
    label: str
    created_at: str
    reminded_at: str


class ReminderStore:
    """Persistent PRIVATE draft reminders. No channel destination exists here."""

    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS private_reminders (
                id TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL,
                due_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                label TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                reminded_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS reminders_due_idx ON private_reminders(status,due_at)")
        self.conn.commit()

    def add(self, draft_id: str, due_at: datetime | None, *, label: str = "") -> ReminderJob:
        job_id = secrets.token_hex(6)
        now = datetime.now(timezone.utc).isoformat()
        due = due_at.astimezone(timezone.utc).isoformat() if due_at is not None else ""
        status = "pending" if due else "pinned"
        with self.conn:
            self.conn.execute(
                "INSERT INTO private_reminders(id,draft_id,due_at,status,label,created_at) VALUES(?,?,?,?,?,?)",
                (job_id, str(draft_id), due, status, str(label)[:100], now),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> ReminderJob | None:
        row = self.conn.execute("SELECT * FROM private_reminders WHERE id=?", (str(job_id),)).fetchone()
        return _row(row) if row is not None else None

    def due(self, now: datetime, *, limit: int = 20) -> list[ReminderJob]:
        value = now.astimezone(timezone.utc).isoformat()
        rows = self.conn.execute(
            "SELECT * FROM private_reminders WHERE status='pending' AND due_at<>'' AND due_at<=? ORDER BY due_at LIMIT ?",
            (value, max(1, min(int(limit), 100))),
        ).fetchall()
        return [_row(row) for row in rows]

    def list_active(self, *, limit: int = 50) -> list[ReminderJob]:
        rows = self.conn.execute(
            "SELECT * FROM private_reminders WHERE status IN ('pending','pinned') ORDER BY CASE WHEN due_at='' THEN 1 ELSE 0 END,due_at,created_at LIMIT ?",
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [_row(row) for row in rows]

    def mark_sent(self, job_id: str, when: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE private_reminders SET status='sent', reminded_at=? WHERE id=? AND status='pending'",
                (when.astimezone(timezone.utc).isoformat(), str(job_id)),
            )

    def cancel(self, job_id: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE private_reminders SET status='cancelled' WHERE id=? AND status IN ('pending','pinned')",
                (str(job_id),),
            )
        return cur.rowcount > 0

    def close(self) -> None:
        self.conn.close()


def _row(row: sqlite3.Row) -> ReminderJob:
    return ReminderJob(
        id=str(row["id"]), draft_id=str(row["draft_id"]), due_at=str(row["due_at"]),
        status=str(row["status"]), label=str(row["label"]), created_at=str(row["created_at"]),
        reminded_at=str(row["reminded_at"]),
    )
