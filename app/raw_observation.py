from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class RawObservationError(RuntimeError):
    """Raw source truth could not be persisted safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RawObservation:
    provider: str
    external_post_id: str
    source_handle: str
    source_mode: str
    created_at: str
    text: str = ""
    conversation_id: str = ""
    reply_to_id: str = ""
    quoted_id: str = ""
    quoted_text: str = ""
    quoted_author: str = ""
    lang: str = ""
    media_json: str = "[]"
    quoted_media_json: str = "[]"
    post_type: str = "post"
    is_retweet: bool = False
    is_reply: bool = False
    is_quote: bool = False
    is_media_only: bool = False
    provenance: str = ""
    retrieval_attempt_id: str = ""
    provider_payload_hash: str = ""
    observation_status: str = "converted"

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip().casefold()
        external_post_id = str(self.external_post_id or "").strip()
        source_handle = str(self.source_handle or "").lstrip("@").strip().casefold()
        if not provider or not external_post_id or not source_handle:
            raise RawObservationError(
                "Raw observation requires provider, external_post_id and source_handle."
            )
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "external_post_id", external_post_id)
        object.__setattr__(self, "source_handle", source_handle)

    @property
    def observation_key(self) -> str:
        return _sha256_text(
            "\x1f".join((self.provider, self.source_handle, self.external_post_id))
        )

    def snapshot_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_retweet"] = bool(self.is_retweet)
        data["is_reply"] = bool(self.is_reply)
        data["is_quote"] = bool(self.is_quote)
        data["is_media_only"] = bool(self.is_media_only)
        return data

    @property
    def snapshot_hash(self) -> str:
        return _sha256_text(_canonical_json(self.snapshot_dict()))


class RawObservationStore:
    """Durable pre-filter source truth.

    `raw_observations` keeps the latest canonical snapshot and observation counters.
    `raw_observation_versions` keeps each distinct provider-visible version once.
    Repeated five-minute scans therefore do not duplicate full payloads indefinitely,
    while edits/representation changes remain auditable.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.conn = sqlite3.connect(self.path, timeout=15)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        except sqlite3.Error as exc:
            raise RawObservationError(
                f"Could not initialize raw observation store: {type(exc).__name__}"
            ) from exc

    def _init_schema(self) -> None:
        try:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS raw_observation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS raw_observations (
                    observation_key TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    external_post_id TEXT NOT NULL,
                    source_handle TEXT NOT NULL,
                    source_mode TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    reply_to_id TEXT NOT NULL DEFAULT '',
                    quoted_id TEXT NOT NULL DEFAULT '',
                    quoted_text TEXT NOT NULL DEFAULT '',
                    quoted_author TEXT NOT NULL DEFAULT '',
                    lang TEXT NOT NULL DEFAULT '',
                    media_json TEXT NOT NULL DEFAULT '[]',
                    quoted_media_json TEXT NOT NULL DEFAULT '[]',
                    post_type TEXT NOT NULL DEFAULT 'post',
                    is_retweet INTEGER NOT NULL DEFAULT 0,
                    is_reply INTEGER NOT NULL DEFAULT 0,
                    is_quote INTEGER NOT NULL DEFAULT 0,
                    is_media_only INTEGER NOT NULL DEFAULT 0,
                    provenance TEXT NOT NULL DEFAULT '',
                    retrieval_attempt_id TEXT NOT NULL DEFAULT '',
                    provider_payload_hash TEXT NOT NULL DEFAULT '',
                    observation_status TEXT NOT NULL DEFAULT 'converted',
                    snapshot_hash TEXT NOT NULL,
                    first_observed_at TEXT NOT NULL,
                    last_observed_at TEXT NOT NULL,
                    observation_count INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(provider, external_post_id, source_handle)
                );

                CREATE INDEX IF NOT EXISTS raw_observations_source_time_idx
                ON raw_observations(source_handle, created_at, external_post_id);

                CREATE INDEX IF NOT EXISTS raw_observations_status_idx
                ON raw_observations(observation_status, last_observed_at);

                CREATE TABLE IF NOT EXISTS raw_observation_versions (
                    observation_key TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    retrieval_attempt_id TEXT NOT NULL DEFAULT '',
                    provenance TEXT NOT NULL DEFAULT '',
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY(observation_key, snapshot_hash),
                    FOREIGN KEY(observation_key)
                        REFERENCES raw_observations(observation_key)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS raw_observation_versions_time_idx
                ON raw_observation_versions(observed_at);
                """
            )
            self.conn.execute(
                """
                INSERT INTO raw_observation_meta(key, value)
                VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            raise RawObservationError(
                f"Could not create raw observation schema: {type(exc).__name__}"
            ) from exc

    def record(
        self,
        observation: RawObservation,
        *,
        observed_at: str | None = None,
    ) -> None:
        now = str(observed_at or _utc_now())
        snapshot = observation.snapshot_dict()
        snapshot_json = _canonical_json(snapshot)
        snapshot_hash = observation.snapshot_hash
        key = observation.observation_key
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO raw_observations(
                        observation_key, provider, external_post_id, source_handle,
                        source_mode, created_at, text, conversation_id, reply_to_id,
                        quoted_id, quoted_text, quoted_author, lang, media_json,
                        quoted_media_json, post_type, is_retweet, is_reply, is_quote,
                        is_media_only, provenance, retrieval_attempt_id,
                        provider_payload_hash, observation_status, snapshot_hash,
                        first_observed_at, last_observed_at, observation_count
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(observation_key) DO UPDATE SET
                        source_mode=excluded.source_mode,
                        created_at=excluded.created_at,
                        text=excluded.text,
                        conversation_id=excluded.conversation_id,
                        reply_to_id=excluded.reply_to_id,
                        quoted_id=excluded.quoted_id,
                        quoted_text=excluded.quoted_text,
                        quoted_author=excluded.quoted_author,
                        lang=excluded.lang,
                        media_json=excluded.media_json,
                        quoted_media_json=excluded.quoted_media_json,
                        post_type=excluded.post_type,
                        is_retweet=excluded.is_retweet,
                        is_reply=excluded.is_reply,
                        is_quote=excluded.is_quote,
                        is_media_only=excluded.is_media_only,
                        provenance=excluded.provenance,
                        retrieval_attempt_id=excluded.retrieval_attempt_id,
                        provider_payload_hash=excluded.provider_payload_hash,
                        observation_status=excluded.observation_status,
                        snapshot_hash=excluded.snapshot_hash,
                        last_observed_at=excluded.last_observed_at,
                        observation_count=raw_observations.observation_count + 1
                    """,
                    (
                        key,
                        observation.provider,
                        observation.external_post_id,
                        observation.source_handle,
                        observation.source_mode,
                        observation.created_at,
                        observation.text,
                        observation.conversation_id,
                        observation.reply_to_id,
                        observation.quoted_id,
                        observation.quoted_text,
                        observation.quoted_author,
                        observation.lang,
                        observation.media_json,
                        observation.quoted_media_json,
                        observation.post_type,
                        int(observation.is_retweet),
                        int(observation.is_reply),
                        int(observation.is_quote),
                        int(observation.is_media_only),
                        observation.provenance,
                        observation.retrieval_attempt_id,
                        observation.provider_payload_hash,
                        observation.observation_status,
                        snapshot_hash,
                        now,
                        now,
                    ),
                )
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO raw_observation_versions(
                        observation_key, snapshot_hash, observed_at,
                        retrieval_attempt_id, provenance, snapshot_json
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        key,
                        snapshot_hash,
                        now,
                        observation.retrieval_attempt_id,
                        observation.provenance,
                        snapshot_json,
                    ),
                )
        except sqlite3.Error as exc:
            raise RawObservationError(
                f"Could not persist raw observation: {type(exc).__name__}"
            ) from exc

    def get(
        self,
        *,
        provider: str,
        external_post_id: str,
        source_handle: str,
    ) -> dict[str, Any] | None:
        key = _sha256_text(
            "\x1f".join(
                (
                    str(provider).strip().casefold(),
                    str(source_handle).lstrip("@").strip().casefold(),
                    str(external_post_id).strip(),
                )
            )
        )
        row = self.conn.execute(
            "SELECT * FROM raw_observations WHERE observation_key=?",
            (key,),
        ).fetchone()
        return dict(row) if row is not None else None

    def count(self, *, source_handle: str = "") -> int:
        if source_handle:
            return int(
                self.conn.execute(
                    "SELECT count(*) FROM raw_observations WHERE source_handle=?",
                    (str(source_handle).lstrip("@").strip().casefold(),),
                ).fetchone()[0]
            )
        return int(self.conn.execute("SELECT count(*) FROM raw_observations").fetchone()[0])

    def version_count(self, observation_key: str) -> int:
        return int(
            self.conn.execute(
                "SELECT count(*) FROM raw_observation_versions WHERE observation_key=?",
                (str(observation_key),),
            ).fetchone()[0]
        )

    def close(self) -> None:
        self.conn.close()
