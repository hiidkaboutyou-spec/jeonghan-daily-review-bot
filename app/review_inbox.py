from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Draft, Update

VALID_STATUSES = {"pending", "ready", "rejected"}


@dataclass(frozen=True, slots=True)
class ReviewItem:
    draft_id: str
    update_id: str
    status: str
    category: str
    source: str
    created_at: str


class ReviewInboxStore:
    """Private-review inbox metadata; captions remain in the existing draft state."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_inbox (
                draft_id TEXT PRIMARY KEY,
                update_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                category TEXT NOT NULL DEFAULT 'general',
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS review_inbox_status_idx ON review_inbox(status, created_at DESC)")
        self.conn.commit()

    def upsert(self, draft: Draft, update: Update | None, *, status: str = "pending") -> None:
        status = status if status in VALID_STATUSES else "pending"
        category = update.category if update is not None else "general"
        source = update.author if update is not None else ""
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO review_inbox(draft_id,update_id,status,category,source,created_at,updated_at)
                VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(draft_id) DO UPDATE SET
                    update_id=excluded.update_id,
                    category=excluded.category,
                    source=excluded.source,
                    created_at=CASE WHEN excluded.created_at<>'' THEN excluded.created_at ELSE review_inbox.created_at END,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (draft.id, draft.update_id, status, category, source, draft.created_at or ""),
            )

    def set_status(self, draft_id: str, status: str) -> bool:
        if status not in VALID_STATUSES:
            return False
        with self.conn:
            cur = self.conn.execute(
                "UPDATE review_inbox SET status=?, updated_at=CURRENT_TIMESTAMP WHERE draft_id=?",
                (status, str(draft_id)),
            )
        return cur.rowcount > 0

    def get(self, draft_id: str) -> ReviewItem | None:
        row = self.conn.execute(
            "SELECT draft_id,update_id,status,category,source,created_at FROM review_inbox WHERE draft_id=?",
            (str(draft_id),),
        ).fetchone()
        return _row_to_item(row) if row is not None else None

    def count(self, status: str = "all", *, category: str = "", source: str = "") -> int:
        where, params = _filters(status, category, source)
        sql = "SELECT count(*) FROM review_inbox"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return int(self.conn.execute(sql, params).fetchone()[0])

    def list_items(
        self,
        *,
        status: str = "pending",
        category: str = "",
        source: str = "",
        page: int = 0,
        page_size: int = 5,
    ) -> tuple[list[ReviewItem], int, int]:
        page_size = max(1, min(int(page_size), 10))
        total = self.count(status, category=category, source=source)
        pages = max(1, (total + page_size - 1) // page_size)
        page = max(0, min(int(page), pages - 1))
        where, params = _filters(status, category, source)
        sql = "SELECT draft_id,update_id,status,category,source,created_at FROM review_inbox"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, draft_id DESC LIMIT ? OFFSET ?"
        rows = self.conn.execute(sql, [*params, page_size, page * page_size]).fetchall()
        return [_row_to_item(row) for row in rows], page, pages

    def sync_from_state(self, drafts: Any, archive: Any) -> int:
        if not isinstance(drafts, dict):
            return 0
        archive = archive if isinstance(archive, dict) else {}
        added = 0
        for raw in drafts.values():
            if not isinstance(raw, dict):
                continue
            try:
                draft = Draft.from_dict(raw)
            except (TypeError, ValueError):
                continue
            if self.get(draft.id) is not None:
                continue
            update = None
            raw_update = archive.get(draft.update_id)
            if isinstance(raw_update, dict):
                try:
                    update = Update.from_dict(raw_update)
                except (TypeError, ValueError):
                    update = None
            self.upsert(draft, update)
            added += 1
        return added

    def close(self) -> None:
        self.conn.close()


def _filters(status: str, category: str, source: str) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if status in VALID_STATUSES:
        where.append("status=?")
        params.append(status)
    if category:
        where.append("category=?")
        params.append(category)
    if source:
        where.append("source=?")
        params.append(source)
    return where, params


def _row_to_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        draft_id=str(row["draft_id"]),
        update_id=str(row["update_id"]),
        status=str(row["status"]),
        category=str(row["category"]),
        source=str(row["source"]),
        created_at=str(row["created_at"]),
    )
