from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class FicObservation:
    work_id: str
    chapters: str
    updated: str


class FicStateStore:
    """Private durable state for nightly fic observations and digest delivery."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fic_work_state (
                work_id TEXT PRIMARY KEY,
                chapters TEXT NOT NULL DEFAULT '',
                updated TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def classify(self, observation: FicObservation, *, now: datetime | None = None) -> str:
        """Return new/updated/unchanged, then persist the current observation.

        This classification is internal evidence; the first run establishes a baseline
        and callers may choose not to present every baseline item as a newly published work.
        """
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT chapters, updated FROM fic_work_state WHERE work_id = ?",
            (observation.work_id,),
        ).fetchone()
        if row is None:
            status = "new"
            self.conn.execute(
                "INSERT INTO fic_work_state(work_id, chapters, updated, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (observation.work_id, observation.chapters, observation.updated, now, now),
            )
        else:
            old_chapters, old_updated = str(row[0]), str(row[1])
            status = "updated" if (old_chapters, old_updated) != (observation.chapters, observation.updated) else "unchanged"
            self.conn.execute(
                "UPDATE fic_work_state SET chapters = ?, updated = ?, last_seen_at = ? WHERE work_id = ?",
                (observation.chapters, observation.updated, now, observation.work_id),
            )
        self.conn.commit()
        return status

    def close(self) -> None:
        self.conn.close()
