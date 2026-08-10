from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

CALLBACK_MAX_BYTES = 64
TOKEN_PREFIX = "cb:"
DEFAULT_TTL = timedelta(days=2)


class CallbackDataError(ValueError):
    """Raised for invalid, unknown, or expired callback data."""


class CallbackStore:
    """Durable mapping for callback payloads that exceed Telegram's 64-byte limit."""

    def __init__(self, path: Path, *, ttl: timedelta = DEFAULT_TTL):
        self.path = Path(path)
        self.ttl = ttl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS callback_tokens (
                token TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    @staticmethod
    def byte_length(value: str) -> int:
        return len(str(value).encode("utf-8"))

    def encode(self, payload: str, *, now: datetime | None = None) -> str:
        payload = str(payload)
        length = self.byte_length(payload)
        if length < 1:
            raise CallbackDataError("Callback data must not be empty.")
        if length <= CALLBACK_MAX_BYTES and not payload.startswith(TOKEN_PREFIX):
            return payload

        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires_at = (now + self.ttl).isoformat()
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        # Start compact, extend deterministically on the extremely unlikely event
        # that an existing token prefix belongs to a different payload.
        for chars in range(24, 61, 4):
            token = TOKEN_PREFIX + digest[:chars]
            row = self.conn.execute(
                "SELECT payload FROM callback_tokens WHERE token = ?", (token,)
            ).fetchone()
            if row is None or row[0] == payload:
                self.conn.execute(
                    "INSERT OR REPLACE INTO callback_tokens(token, payload, expires_at) VALUES (?, ?, ?)",
                    (token, payload, expires_at),
                )
                self.conn.commit()
                return token
        raise CallbackDataError("Unable to allocate a collision-free callback token.")

    def decode(self, value: str, *, now: datetime | None = None) -> str:
        value = str(value)
        length = self.byte_length(value)
        if length < 1 or length > CALLBACK_MAX_BYTES:
            raise CallbackDataError("Callback data has an invalid UTF-8 byte length.")
        if not value.startswith(TOKEN_PREFIX):
            return value

        row = self.conn.execute(
            "SELECT payload, expires_at FROM callback_tokens WHERE token = ?", (value,)
        ).fetchone()
        if row is None:
            raise CallbackDataError("Callback token is unknown or expired.")
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        try:
            expires = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
        except ValueError:
            expires = datetime.min.replace(tzinfo=timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires.astimezone(timezone.utc) <= now:
            self.conn.execute("DELETE FROM callback_tokens WHERE token = ?", (value,))
            self.conn.commit()
            raise CallbackDataError("Callback token is unknown or expired.")
        return str(row[0])

    def prune(self, *, now: datetime | None = None) -> None:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        self.conn.execute("DELETE FROM callback_tokens WHERE expires_at <= ?", (now,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
