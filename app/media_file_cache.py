from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import MediaItem

VIDEO_CACHE_VERSION = "telegram-ios-video-v2"


@dataclass(frozen=True, slots=True)
class CachedTelegramMedia:
    media_key: str
    kind: str
    file_id: str
    file_unique_id: str = ""


class MediaFileCache:
    """Persistent Telegram file_id cache for the private review chat only."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_media_cache (
                media_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                original_url TEXT NOT NULL,
                file_id TEXT NOT NULL,
                file_unique_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.commit()

    @staticmethod
    def key_for(item: MediaItem) -> str:
        version = VIDEO_CACHE_VERSION if item.kind == "video" else "photo-v1"
        payload = f"{version}\n{item.kind}\n{item.url}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, item: MediaItem) -> CachedTelegramMedia | None:
        key = self.key_for(item)
        row = self.conn.execute(
            "SELECT media_key, kind, file_id, file_unique_id FROM telegram_media_cache WHERE media_key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return CachedTelegramMedia(
            media_key=str(row["media_key"]),
            kind=str(row["kind"]),
            file_id=str(row["file_id"]),
            file_unique_id=str(row["file_unique_id"] or ""),
        )

    def get_all(self, media: list[MediaItem]) -> list[CachedTelegramMedia] | None:
        if not media:
            return []
        result: list[CachedTelegramMedia] = []
        for item in media:
            cached = self.get(item)
            if cached is None or cached.kind != item.kind:
                return None
            result.append(cached)
        return result

    def put(self, item: MediaItem, file_id: str, file_unique_id: str = "") -> None:
        if not file_id:
            return
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO telegram_media_cache(media_key,kind,original_url,file_id,file_unique_id,updated_at)
                VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
                ON CONFLICT(media_key) DO UPDATE SET
                    kind=excluded.kind,
                    original_url=excluded.original_url,
                    file_id=excluded.file_id,
                    file_unique_id=excluded.file_unique_id,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (self.key_for(item), item.kind, item.url, file_id, file_unique_id),
            )

    def delete(self, item: MediaItem) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM telegram_media_cache WHERE media_key=?", (self.key_for(item),))

    def close(self) -> None:
        self.conn.close()
