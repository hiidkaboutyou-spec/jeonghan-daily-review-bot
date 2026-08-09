from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .models import MediaItem


DEFAULT_DEDUP_HOURS = 72
MAX_LEDGER_AGE_DAYS = 30


class MediaDeliveryLedger:
    """Persistent exact-media delivery receipts for the private review chat.

    Telegram has no application-supplied idempotency key for sendPhoto/sendVideo/
    sendMediaGroup. The bot therefore records successful deliveries and checks three
    source-authorized identities before sending again:

    * source media URL identity;
    * SHA-256 of downloaded bytes, when available;
    * Telegram file_unique_id, when Telegram has already seen the file.

    Receipts expire for dedup purposes so legitimately reused media is not suppressed
    forever. Rows are also pruned to keep the SQLite state bounded.
    """

    def __init__(self, path: Path, *, dedup_hours: int = DEFAULT_DEDUP_HOURS):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.dedup_hours = max(1, int(dedup_hours))
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_media_delivery (
                identity TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                update_id TEXT NOT NULL,
                delivered_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_media_delivery_time "
            "ON telegram_media_delivery(delivered_at)"
        )
        self.conn.commit()
        self.prune()

    @staticmethod
    def url_identity(item: MediaItem) -> str:
        payload = f"{item.kind}\n{item.url}".encode("utf-8")
        return "url:" + hashlib.sha256(payload).hexdigest()

    @staticmethod
    def content_identity(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def telegram_identity(file_unique_id: str) -> str:
        value = str(file_unique_id or "").strip()
        return f"telegram:{value}" if value else ""

    def identities_for(
        self,
        item: MediaItem,
        *,
        content_identity: str = "",
        file_unique_id: str = "",
    ) -> tuple[str, ...]:
        values = [self.url_identity(item)]
        if content_identity:
            values.append(str(content_identity))
        telegram = self.telegram_identity(file_unique_id)
        if telegram:
            values.append(telegram)
        return tuple(dict.fromkeys(values))

    def any_recent(self, identities: Iterable[str], *, now: datetime | None = None) -> bool:
        values = [str(value) for value in identities if str(value)]
        if not values:
            return False
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = (current - timedelta(hours=self.dedup_hours)).isoformat()
        placeholders = ",".join("?" for _ in values)
        row = self.conn.execute(
            f"SELECT 1 FROM telegram_media_delivery "
            f"WHERE identity IN ({placeholders}) AND delivered_at >= ? LIMIT 1",
            (*values, cutoff),
        ).fetchone()
        return row is not None

    def mark_delivered(
        self,
        identities: Iterable[str],
        *,
        kind: str,
        update_id: str,
        delivered_at: datetime | None = None,
    ) -> None:
        values = [str(value) for value in identities if str(value)]
        if not values:
            return
        timestamp = (delivered_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO telegram_media_delivery(identity,kind,update_id,delivered_at)
                VALUES(?,?,?,?)
                ON CONFLICT(identity) DO UPDATE SET
                    kind=excluded.kind,
                    update_id=excluded.update_id,
                    delivered_at=excluded.delivered_at
                """,
                [(value, str(kind), str(update_id), timestamp) for value in values],
            )

    def prune(self, *, now: datetime | None = None) -> None:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cutoff = (current - timedelta(days=MAX_LEDGER_AGE_DAYS)).isoformat()
        with self.conn:
            self.conn.execute(
                "DELETE FROM telegram_media_delivery WHERE delivered_at < ?",
                (cutoff,),
            )

    def close(self) -> None:
        self.conn.close()
