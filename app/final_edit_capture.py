from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FINAL_EDIT_CAPTURE_VERSION = 1
FINAL_EDIT_CAPTURE_MODE = "capture_only"
FINAL_EDIT_PROVENANCE = "user_confirmed"
DEFAULT_EDIT_TTL_SECONDS = 30 * 60
MAX_FINAL_EDIT_CHARS = 20_000

PENDING_EDIT = "pending_edit"
AWAITING_CONFIRMATION = "awaiting_confirmation"
CONFIRMED_FINAL_EDIT = "confirmed_final_edit"
CANCELLED = "cancelled"
EXPIRED = "expired"
_SESSION_STATES = {PENDING_EDIT, AWAITING_CONFIRMATION, CONFIRMED_FINAL_EDIT, CANCELLED, EXPIRED}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fingerprint(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\x1f{value}".encode("utf-8")).hexdigest()


def privacy_ref(namespace: str, value: object) -> str:
    return fingerprint(namespace, str(value or ""))[:24]


def make_session_id(draft_id: str, now: datetime | None = None) -> str:
    stamp = iso_utc(now)
    return "fes_" + fingerprint("final-edit-session-v1", f"{draft_id}\x1f{stamp}")[:20]


def make_final_edit_id(draft_id: str, final_fingerprint: str, confirmed_at: str) -> str:
    seed = f"{draft_id}\x1f{final_fingerprint}\x1f{confirmed_at}"
    return "fed_" + fingerprint("final-edit-record-v1", seed)[:24]


@dataclass(frozen=True, slots=True)
class FinalEditSession:
    session_id: str
    draft_id: str
    update_id: str
    event_id: str
    segment_id: str
    review_chat_ref: str
    authoritative_review_draft_fingerprint: str
    original_factual_fingerprint: str
    shadow_style_candidate_fingerprint: str
    content_type: str
    prompt_message_id: int
    candidate_fingerprint: str
    status: str
    created_at: str
    expires_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class FinalEditRecord:
    final_edit_id: str
    draft_id: str
    update_id: str
    event_id: str
    segment_id: str
    review_chat_ref: str
    original_factual_fingerprint: str
    shadow_style_candidate_fingerprint: str
    authoritative_review_draft_fingerprint: str
    final_user_edit_fingerprint: str
    content_type: str
    created_at: str
    confirmed_at: str
    confirmation_status: str
    edit_provenance: str
    supersedes_final_edit_id: str
    revoked: bool
    active: bool
    calibration_eligible: str
    calibration_metadata: dict[str, Any]


class FinalEditStore:
    """Canonical private-review owner for confirmed final user-edit bodies.

    Full private text exists only in this private-review SQLite database. Generic
    durable StateStore/Event/Calibration metadata receives fingerprints/IDs only.
    This store owns no retrieval, delivery, seen state, receipts, media, or style
    authority.
    """

    def __init__(self, path: Path, *, ttl_seconds: int = DEFAULT_EDIT_TTL_SECONDS):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(60, min(int(ttl_seconds), 24 * 60 * 60))
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS final_edit_sessions (
                    session_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    event_id TEXT NOT NULL DEFAULT '',
                    segment_id TEXT NOT NULL DEFAULT '',
                    review_chat_ref TEXT NOT NULL,
                    authoritative_review_draft_fingerprint TEXT NOT NULL,
                    original_factual_fingerprint TEXT NOT NULL DEFAULT '',
                    shadow_style_candidate_fingerprint TEXT NOT NULL DEFAULT '',
                    content_type TEXT NOT NULL DEFAULT 'general',
                    prompt_message_id INTEGER NOT NULL DEFAULT 0,
                    candidate_body TEXT,
                    candidate_fingerprint TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS final_edit_sessions_prompt_idx "
                "ON final_edit_sessions(prompt_message_id,status,expires_at)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS final_edit_sessions_draft_idx "
                "ON final_edit_sessions(draft_id,created_at DESC)"
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS final_edits (
                    final_edit_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    event_id TEXT NOT NULL DEFAULT '',
                    segment_id TEXT NOT NULL DEFAULT '',
                    review_chat_ref TEXT NOT NULL,
                    original_factual_fingerprint TEXT NOT NULL DEFAULT '',
                    shadow_style_candidate_fingerprint TEXT NOT NULL DEFAULT '',
                    authoritative_review_draft_fingerprint TEXT NOT NULL,
                    final_user_edit_fingerprint TEXT NOT NULL,
                    final_body TEXT NOT NULL,
                    content_type TEXT NOT NULL DEFAULT 'general',
                    created_at TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    confirmation_status TEXT NOT NULL,
                    edit_provenance TEXT NOT NULL,
                    supersedes_final_edit_id TEXT NOT NULL DEFAULT '',
                    revoked INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    calibration_eligible TEXT NOT NULL DEFAULT 'undecided',
                    calibration_metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS final_edits_active_idx "
                "ON final_edits(draft_id,active,revoked,confirmed_at DESC)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS final_edits_content_idx "
                "ON final_edits(content_type,active,revoked)"
            )

    def start_session(
        self,
        *,
        draft_id: str,
        update_id: str,
        review_chat_ref: str,
        authoritative_review_draft_fingerprint: str,
        event_id: str = "",
        segment_id: str = "",
        original_factual_fingerprint: str = "",
        shadow_style_candidate_fingerprint: str = "",
        content_type: str = "general",
        now: datetime | None = None,
    ) -> FinalEditSession:
        now = now or utc_now()
        self.expire_stale(now=now)
        self.cancel_open_for_draft(draft_id, now=now)
        session_id = make_session_id(str(draft_id), now)
        created = iso_utc(now)
        expires = iso_utc(now + timedelta(seconds=self.ttl_seconds))
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO final_edit_sessions(
                    session_id,draft_id,update_id,event_id,segment_id,review_chat_ref,
                    authoritative_review_draft_fingerprint,original_factual_fingerprint,
                    shadow_style_candidate_fingerprint,content_type,prompt_message_id,
                    candidate_body,candidate_fingerprint,status,created_at,expires_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,0,NULL,'',?,?,?,?)
                """,
                (
                    session_id, str(draft_id), str(update_id), str(event_id or "")[:80],
                    str(segment_id or "")[:80], str(review_chat_ref)[:64],
                    str(authoritative_review_draft_fingerprint)[:64],
                    str(original_factual_fingerprint or "")[:64],
                    str(shadow_style_candidate_fingerprint or "")[:64],
                    str(content_type or "general")[:80], PENDING_EDIT, created, expires, created,
                ),
            )
        return self.get_session(session_id, expire=False)  # type: ignore[return-value]

    def set_prompt_message(self, session_id: str, message_id: int, *, now: datetime | None = None) -> bool:
        now_text = iso_utc(now)
        with self.conn:
            cur = self.conn.execute(
                "UPDATE final_edit_sessions SET prompt_message_id=?,updated_at=? "
                "WHERE session_id=? AND status=?",
                (max(0, int(message_id)), now_text, str(session_id), PENDING_EDIT),
            )
        return cur.rowcount > 0

    def find_live_by_prompt(
        self,
        prompt_message_id: int,
        *,
        review_chat_ref: str,
        now: datetime | None = None,
    ) -> FinalEditSession | None:
        self.expire_stale(now=now)
        row = self.conn.execute(
            """
            SELECT * FROM final_edit_sessions
            WHERE prompt_message_id=? AND review_chat_ref=? AND status=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (int(prompt_message_id), str(review_chat_ref), PENDING_EDIT),
        ).fetchone()
        return self._session(row) if row is not None else None

    def receive_user_text(
        self,
        session_id: str,
        text: str,
        *,
        current_draft_fingerprint: str,
        review_chat_ref: str,
        now: datetime | None = None,
    ) -> FinalEditSession | None:
        now = now or utc_now()
        session = self.get_session(session_id, now=now)
        if session is None or session.status != PENDING_EDIT:
            return None
        if session.review_chat_ref != str(review_chat_ref):
            return None
        if session.authoritative_review_draft_fingerprint != str(current_draft_fingerprint):
            return None
        body = str(text or "").strip()
        if not body or len(body) > MAX_FINAL_EDIT_CHARS:
            return None
        candidate_fp = fingerprint("final-user-edit-v1", body)
        now_text = iso_utc(now)
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE final_edit_sessions
                SET candidate_body=?,candidate_fingerprint=?,status=?,updated_at=?
                WHERE session_id=? AND status=?
                """,
                (body, candidate_fp, AWAITING_CONFIRMATION, now_text, session_id, PENDING_EDIT),
            )
        if cur.rowcount != 1:
            return None
        return self.get_session(session_id, expire=False)

    def candidate_body(self, session_id: str) -> str:
        row = self.conn.execute(
            "SELECT candidate_body FROM final_edit_sessions WHERE session_id=?",
            (str(session_id),),
        ).fetchone()
        return str(row[0] or "") if row is not None else ""

    def replace_candidate(self, session_id: str, *, now: datetime | None = None) -> bool:
        session = self.get_session(session_id, now=now)
        if session is None or session.status != AWAITING_CONFIRMATION:
            return False
        now = now or utc_now()
        expires = iso_utc(now + timedelta(seconds=self.ttl_seconds))
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE final_edit_sessions
                SET candidate_body=NULL,candidate_fingerprint='',prompt_message_id=0,
                    status=?,expires_at=?,updated_at=?
                WHERE session_id=? AND status=?
                """,
                (PENDING_EDIT, expires, iso_utc(now), str(session_id), AWAITING_CONFIRMATION),
            )
        return cur.rowcount == 1

    def cancel_session(self, session_id: str, *, now: datetime | None = None) -> bool:
        now_text = iso_utc(now)
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE final_edit_sessions
                SET status=?,candidate_body=NULL,candidate_fingerprint='',updated_at=?
                WHERE session_id=? AND status IN (?,?)
                """,
                (CANCELLED, now_text, str(session_id), PENDING_EDIT, AWAITING_CONFIRMATION),
            )
        return cur.rowcount > 0

    def cancel_open_for_draft(self, draft_id: str, *, now: datetime | None = None) -> int:
        now_text = iso_utc(now)
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE final_edit_sessions
                SET status=?,candidate_body=NULL,candidate_fingerprint='',updated_at=?
                WHERE draft_id=? AND status IN (?,?)
                """,
                (CANCELLED, now_text, str(draft_id), PENDING_EDIT, AWAITING_CONFIRMATION),
            )
        return int(cur.rowcount)

    def expire_stale(self, *, now: datetime | None = None) -> int:
        now_text = iso_utc(now)
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE final_edit_sessions
                SET status=?,candidate_body=NULL,candidate_fingerprint='',updated_at=?
                WHERE status IN (?,?) AND expires_at<=?
                """,
                (EXPIRED, now_text, PENDING_EDIT, AWAITING_CONFIRMATION, now_text),
            )
        return int(cur.rowcount)

    def confirm_session(
        self,
        session_id: str,
        *,
        current_draft_fingerprint: str,
        review_chat_ref: str,
        calibration_eligible: str = "undecided",
        calibration_metadata: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> FinalEditRecord | None:
        now = now or utc_now()
        session = self.get_session(session_id, now=now)
        if session is None or session.status != AWAITING_CONFIRMATION:
            return None
        if session.review_chat_ref != str(review_chat_ref):
            return None
        if session.authoritative_review_draft_fingerprint != str(current_draft_fingerprint):
            return None
        body = self.candidate_body(session_id)
        if not body:
            return None
        body_fp = fingerprint("final-user-edit-v1", body)
        if body_fp != session.candidate_fingerprint:
            return None
        confirmed_at = iso_utc(now)
        final_edit_id = make_final_edit_id(session.draft_id, body_fp, confirmed_at)
        previous = self.latest_active(session.draft_id)
        previous_id = previous.final_edit_id if previous is not None else ""
        eligible = str(calibration_eligible or "undecided")
        if eligible not in {"undecided", "eligible", "ineligible"}:
            eligible = "undecided"
        metadata = calibration_metadata if isinstance(calibration_metadata, dict) else {}
        metadata_text = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self.conn:
            if previous_id:
                self.conn.execute("UPDATE final_edits SET active=0 WHERE final_edit_id=?", (previous_id,))
            self.conn.execute(
                """
                INSERT INTO final_edits(
                    final_edit_id,draft_id,update_id,event_id,segment_id,review_chat_ref,
                    original_factual_fingerprint,shadow_style_candidate_fingerprint,
                    authoritative_review_draft_fingerprint,final_user_edit_fingerprint,
                    final_body,content_type,created_at,confirmed_at,confirmation_status,
                    edit_provenance,supersedes_final_edit_id,revoked,active,
                    calibration_eligible,calibration_metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,?,?)
                """,
                (
                    final_edit_id, session.draft_id, session.update_id, session.event_id,
                    session.segment_id, session.review_chat_ref,
                    session.original_factual_fingerprint,
                    session.shadow_style_candidate_fingerprint,
                    session.authoritative_review_draft_fingerprint, body_fp, body,
                    session.content_type, session.created_at, confirmed_at,
                    CONFIRMED_FINAL_EDIT, FINAL_EDIT_PROVENANCE, previous_id,
                    eligible, metadata_text,
                ),
            )
            self.conn.execute(
                """
                UPDATE final_edit_sessions
                SET status=?,candidate_body=NULL,updated_at=? WHERE session_id=?
                """,
                (CONFIRMED_FINAL_EDIT, confirmed_at, session.session_id),
            )
        return self.get_final_edit(final_edit_id)

    def update_calibration_metadata(
        self,
        final_edit_id: str,
        *,
        eligible: str,
        metadata: dict[str, Any],
    ) -> bool:
        value = eligible if eligible in {"undecided", "eligible", "ineligible"} else "undecided"
        encoded = json.dumps(metadata if isinstance(metadata, dict) else {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with self.conn:
            cur = self.conn.execute(
                "UPDATE final_edits SET calibration_eligible=?,calibration_metadata_json=? WHERE final_edit_id=?",
                (value, encoded, str(final_edit_id)),
            )
        return cur.rowcount == 1

    def revoke_final_edit(self, final_edit_id: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE final_edits SET revoked=1,active=0 WHERE final_edit_id=?",
                (str(final_edit_id),),
            )
        return cur.rowcount == 1

    def latest_active(self, draft_id: str) -> FinalEditRecord | None:
        row = self.conn.execute(
            """
            SELECT * FROM final_edits
            WHERE draft_id=? AND active=1 AND revoked=0 AND confirmation_status=?
            ORDER BY confirmed_at DESC LIMIT 1
            """,
            (str(draft_id), CONFIRMED_FINAL_EDIT),
        ).fetchone()
        return self._record(row) if row is not None else None

    def get_final_edit(self, final_edit_id: str) -> FinalEditRecord | None:
        row = self.conn.execute("SELECT * FROM final_edits WHERE final_edit_id=?", (str(final_edit_id),)).fetchone()
        return self._record(row) if row is not None else None

    def final_body(self, final_edit_id: str) -> str:
        row = self.conn.execute("SELECT final_body FROM final_edits WHERE final_edit_id=?", (str(final_edit_id),)).fetchone()
        return str(row[0] or "") if row is not None else ""

    def confirmed_real_final_edit_count(self) -> int:
        return int(
            self.conn.execute(
                "SELECT count(*) FROM final_edits WHERE active=1 AND revoked=0 AND confirmation_status=? AND edit_provenance=?",
                (CONFIRMED_FINAL_EDIT, FINAL_EDIT_PROVENANCE),
            ).fetchone()[0]
        )

    def confirmed_counts_by_content_type(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT content_type,count(*) AS total FROM final_edits
            WHERE active=1 AND revoked=0 AND confirmation_status=? AND edit_provenance=?
            GROUP BY content_type ORDER BY content_type
            """,
            (CONFIRMED_FINAL_EDIT, FINAL_EDIT_PROVENANCE),
        ).fetchall()
        return {str(row["content_type"]): int(row["total"]) for row in rows}

    def get_session(
        self,
        session_id: str,
        *,
        now: datetime | None = None,
        expire: bool = True,
    ) -> FinalEditSession | None:
        if expire:
            self.expire_stale(now=now)
        row = self.conn.execute("SELECT * FROM final_edit_sessions WHERE session_id=?", (str(session_id),)).fetchone()
        return self._session(row) if row is not None else None

    def session_metadata(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id, expire=False)
        if session is None:
            return {}
        return {
            "version": FINAL_EDIT_CAPTURE_VERSION,
            "mode": FINAL_EDIT_CAPTURE_MODE,
            "session_id": session.session_id,
            "draft_id": session.draft_id,
            "update_id": session.update_id,
            "event_id": session.event_id,
            "segment_id": session.segment_id,
            "review_chat_ref": session.review_chat_ref,
            "authoritative_review_draft_fingerprint": session.authoritative_review_draft_fingerprint,
            "original_factual_fingerprint": session.original_factual_fingerprint,
            "shadow_style_candidate_fingerprint": session.shadow_style_candidate_fingerprint,
            "candidate_fingerprint": session.candidate_fingerprint,
            "content_type": session.content_type,
            "status": session.status,
            "created_at": session.created_at,
            "expires_at": session.expires_at,
            "text_persisted_in_generic_state": False,
            "auto_learn": False,
        }

    def _session(self, row: sqlite3.Row) -> FinalEditSession:
        status = str(row["status"])
        if status not in _SESSION_STATES:
            status = CANCELLED
        return FinalEditSession(
            session_id=str(row["session_id"]), draft_id=str(row["draft_id"]),
            update_id=str(row["update_id"]), event_id=str(row["event_id"]),
            segment_id=str(row["segment_id"]), review_chat_ref=str(row["review_chat_ref"]),
            authoritative_review_draft_fingerprint=str(row["authoritative_review_draft_fingerprint"]),
            original_factual_fingerprint=str(row["original_factual_fingerprint"]),
            shadow_style_candidate_fingerprint=str(row["shadow_style_candidate_fingerprint"]),
            content_type=str(row["content_type"]), prompt_message_id=int(row["prompt_message_id"] or 0),
            candidate_fingerprint=str(row["candidate_fingerprint"]), status=status,
            created_at=str(row["created_at"]), expires_at=str(row["expires_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _record(self, row: sqlite3.Row) -> FinalEditRecord:
        try:
            metadata = json.loads(str(row["calibration_metadata_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return FinalEditRecord(
            final_edit_id=str(row["final_edit_id"]), draft_id=str(row["draft_id"]),
            update_id=str(row["update_id"]), event_id=str(row["event_id"]),
            segment_id=str(row["segment_id"]), review_chat_ref=str(row["review_chat_ref"]),
            original_factual_fingerprint=str(row["original_factual_fingerprint"]),
            shadow_style_candidate_fingerprint=str(row["shadow_style_candidate_fingerprint"]),
            authoritative_review_draft_fingerprint=str(row["authoritative_review_draft_fingerprint"]),
            final_user_edit_fingerprint=str(row["final_user_edit_fingerprint"]),
            content_type=str(row["content_type"]), created_at=str(row["created_at"]),
            confirmed_at=str(row["confirmed_at"]), confirmation_status=str(row["confirmation_status"]),
            edit_provenance=str(row["edit_provenance"]),
            supersedes_final_edit_id=str(row["supersedes_final_edit_id"]),
            revoked=bool(row["revoked"]), active=bool(row["active"]),
            calibration_eligible=str(row["calibration_eligible"]), calibration_metadata=metadata,
        )

    def close(self) -> None:
        self.conn.close()


def record_metadata(record: FinalEditRecord) -> dict[str, Any]:
    """Privacy-safe metadata: intentionally excludes final_body."""
    return {
        "version": FINAL_EDIT_CAPTURE_VERSION,
        "mode": FINAL_EDIT_CAPTURE_MODE,
        "final_edit_id": record.final_edit_id,
        "draft_id": record.draft_id,
        "update_id": record.update_id,
        "event_id": record.event_id,
        "segment_id": record.segment_id,
        "review_chat_ref": record.review_chat_ref,
        "original_factual_fingerprint": record.original_factual_fingerprint,
        "shadow_style_candidate_fingerprint": record.shadow_style_candidate_fingerprint,
        "authoritative_review_draft_fingerprint": record.authoritative_review_draft_fingerprint,
        "final_user_edit_fingerprint": record.final_user_edit_fingerprint,
        "content_type": record.content_type,
        "created_at": record.created_at,
        "confirmed_at": record.confirmed_at,
        "confirmation_status": record.confirmation_status,
        "edit_provenance": record.edit_provenance,
        "supersedes_final_edit_id": record.supersedes_final_edit_id,
        "revoked": record.revoked,
        "active": record.active,
        "calibration_eligible": record.calibration_eligible,
        "calibration_metadata": record.calibration_metadata,
        "text_persisted_in_generic_state": False,
        "auto_learn": False,
    }


def metadata_contains_private_body(value: Any) -> bool:
    forbidden = {"final_body", "candidate_body", "final_user_text", "factual_text", "candidate_text"}
    if isinstance(value, dict):
        return any(str(key) in forbidden or metadata_contains_private_body(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(metadata_contains_private_body(item) for item in value)
    return False
