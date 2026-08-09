from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path


class MessageDeliveryStore:
    """Durable receipts for idempotent retry of explicitly keyed multipart messages."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_delivery_parts (
                delivery_key TEXT NOT NULL,
                part_index INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                message_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(delivery_key, part_index)
            )
            """
        )
        self.conn.commit()

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def confirmed_message_id(self, delivery_key: str, part_index: int, text: str) -> int | None:
        row = self.conn.execute(
            "SELECT content_sha256, message_id FROM message_delivery_parts WHERE delivery_key = ? AND part_index = ?",
            (delivery_key, int(part_index)),
        ).fetchone()
        if row is None or str(row[0]) != self.content_hash(text):
            return None
        return int(row[1] or 0)

    def confirm(self, delivery_key: str, part_index: int, text: str, message_id: int) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO message_delivery_parts(delivery_key, part_index, content_sha256, message_id)
            VALUES (?, ?, ?, ?)
            """,
            (delivery_key, int(part_index), self.content_hash(text), int(message_id or 0)),
        )
        self.conn.commit()

    def clear(self, delivery_key: str) -> None:
        self.conn.execute("DELETE FROM message_delivery_parts WHERE delivery_key = ?", (delivery_key,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
