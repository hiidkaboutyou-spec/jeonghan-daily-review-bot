from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import Draft, Update, ensure_utc

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1


class ArchiveStore:
    """Durable local archive index for private-review retrieval.

    JSON state remains the compatibility/source-of-truth fallback. SQLite is a
    searchable index and private-review persistence layer; it can be rebuilt from
    the preserved JSON archive at any time.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = self._open_or_recover()
        self._init_schema()

    def _open_or_recover(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA quick_check").fetchone()
            return conn
        except sqlite3.DatabaseError:
            try:
                conn.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass
            if self.path.exists():
                stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                broken = self.path.with_name(f"{self.path.stem}.broken-{stamp}{self.path.suffix}")
                try:
                    self.path.replace(broken)
                except OSError:
                    self.path.unlink(missing_ok=True)
            conn = sqlite3.connect(self.path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS archive_records (
                update_id TEXT PRIMARY KEY,
                url TEXT NOT NULL DEFAULT '',
                author TEXT NOT NULL DEFAULT '',
                author_name TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                translated_text TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                reply_to_id TEXT NOT NULL DEFAULT '',
                quoted_id TEXT NOT NULL DEFAULT '',
                lang TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                event_key TEXT NOT NULL DEFAULT '',
                event_title TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS archive_fts USING fts5(
                update_id UNINDEXED,
                text,
                translated_text,
                caption,
                author,
                author_name,
                source,
                category,
                event_title,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def count(self) -> int:
        return int(self.conn.execute("SELECT count(*) FROM archive_records").fetchone()[0])

    def index_update(self, update: Update, *, translated_text: str = "", caption: str = "") -> None:
        payload = update.to_dict()
        source = update.raw_query or update.author
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO archive_records(
                    update_id,url,author,author_name,text,translated_text,caption,created_at,
                    conversation_id,reply_to_id,quoted_id,lang,category,event_key,event_title,
                    source,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(update_id) DO UPDATE SET
                    url=excluded.url,
                    author=excluded.author,
                    author_name=excluded.author_name,
                    text=excluded.text,
                    translated_text=CASE WHEN excluded.translated_text<>'' THEN excluded.translated_text ELSE archive_records.translated_text END,
                    caption=CASE WHEN excluded.caption<>'' THEN excluded.caption ELSE archive_records.caption END,
                    created_at=excluded.created_at,
                    conversation_id=excluded.conversation_id,
                    reply_to_id=excluded.reply_to_id,
                    quoted_id=excluded.quoted_id,
                    lang=excluded.lang,
                    category=excluded.category,
                    event_key=excluded.event_key,
                    event_title=excluded.event_title,
                    source=excluded.source,
                    raw_json=excluded.raw_json
                """,
                (
                    update.id,
                    update.url,
                    update.author,
                    update.author_name,
                    update.text,
                    translated_text,
                    caption,
                    update.created_at.isoformat(),
                    update.conversation_id,
                    update.reply_to_id,
                    update.quoted_id,
                    update.lang,
                    update.category,
                    update.event_key,
                    update.event_title,
                    source,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            self._refresh_fts(update.id)

    def _refresh_fts(self, update_id: str) -> None:
        self.conn.execute("DELETE FROM archive_fts WHERE update_id=?", (str(update_id),))
        self.conn.execute(
            """
            INSERT INTO archive_fts(update_id,text,translated_text,caption,author,author_name,source,category,event_title)
            SELECT update_id,text,translated_text,caption,author,author_name,source,category,event_title
            FROM archive_records WHERE update_id=?
            """,
            (str(update_id),),
        )

    def update_caption(self, update_id: str, caption: str) -> None:
        if not caption:
            return
        with self.conn:
            self.conn.execute("UPDATE archive_records SET caption=? WHERE update_id=?", (caption, str(update_id)))
            self._refresh_fts(str(update_id))

    def sync_from_json(self, archive: Any) -> int:
        if not isinstance(archive, dict):
            return 0
        indexed = 0
        existing = {row[0] for row in self.conn.execute("SELECT update_id FROM archive_records")}
        for update_id, raw in archive.items():
            if str(update_id) in existing or not isinstance(raw, dict):
                continue
            try:
                update = Update.from_dict(raw)
            except (TypeError, ValueError, KeyError):
                continue
            self.index_update(update)
            indexed += 1
        return indexed

    def sync_drafts(self, drafts: Any) -> int:
        if not isinstance(drafts, dict):
            return 0
        updated = 0
        for raw in drafts.values():
            if not isinstance(raw, dict):
                continue
            try:
                draft = Draft.from_dict(raw)
            except (TypeError, ValueError):
                continue
            if draft.caption:
                before = self.conn.total_changes
                self.update_caption(draft.update_id, draft.caption)
                if self.conn.total_changes > before:
                    updated += 1
        return updated

    def rebuild(self, archive: Any) -> int:
        with self.conn:
            self.conn.execute("DELETE FROM archive_fts")
            self.conn.execute("DELETE FROM archive_records")
        return self.sync_from_json(archive)

    def search(
        self,
        query: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 80,
    ) -> list[Update]:
        limit = max(1, min(int(limit), 500))
        match = _fts_query(query)
        params: list[Any] = []
        where: list[str] = []
        if start is not None:
            where.append("r.created_at >= ?")
            params.append(ensure_utc(start).isoformat())
        if end is not None:
            where.append("r.created_at < ?")
            params.append(ensure_utc(end).isoformat())

        if match:
            sql = """
                SELECT r.raw_json
                FROM archive_fts f
                JOIN archive_records r ON r.update_id=f.update_id
                WHERE archive_fts MATCH ?
            """
            sql_params: list[Any] = [match]
            if where:
                sql += " AND " + " AND ".join(where)
                sql_params.extend(params)
            sql += " ORDER BY bm25(archive_fts), r.created_at DESC LIMIT ?"
            sql_params.append(limit)
        else:
            sql = "SELECT r.raw_json FROM archive_records r"
            sql_params = list(params)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY r.created_at DESC LIMIT ?"
            sql_params.append(limit)

        found: list[Update] = []
        try:
            rows = self.conn.execute(sql, sql_params).fetchall()
        except sqlite3.DatabaseError as exc:
            logger.warning("Local archive search failed; caller may fall back to X: %s", type(exc).__name__)
            return []
        for row in rows:
            try:
                raw = json.loads(row[0])
                found.append(Update.from_dict(raw))
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                continue
        return found


def _fts_query(value: str) -> str:
    # Keep the query deliberately simple so arbitrary user text cannot become FTS syntax.
    tokens = re.findall(r"[\w\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", value or "", flags=re.UNICODE)
    clean = [token for token in tokens if len(token) > 1][:20]
    return " AND ".join(f'"{token.replace(chr(34), "")}"*' for token in clean)
