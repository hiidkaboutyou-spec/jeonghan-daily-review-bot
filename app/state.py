from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Draft, Update

SCHEMA_VERSION = 1


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _fresh(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "telegram_offset": 0,
            "last_auto_run": "",
            "seen": {},
            "archive": {},
            "sessions": {},
            "drafts": {},
            "awaiting": {},
            "pending_delivery": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._fresh()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return self._fresh()
        fresh = self._fresh()
        if isinstance(value, dict):
            fresh.update(value)
        fresh["schema"] = SCHEMA_VERSION
        return fresh

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, separators=(",", ":"))
        fd, temp_name = tempfile.mkstemp(prefix="state-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @property
    def telegram_offset(self) -> int:
        return int(self.data.get("telegram_offset", 0))

    @telegram_offset.setter
    def telegram_offset(self, value: int) -> None:
        self.data["telegram_offset"] = int(value)

    def mark_seen(self, update: Update) -> None:
        self.data["seen"][update.id] = datetime.now(timezone.utc).isoformat()
        self.data["archive"][update.id] = update.to_dict()

    def is_seen(self, update_id: str) -> bool:
        return str(update_id) in self.data["seen"]

    def archive_update(self, update: Update) -> None:
        self.data["archive"][update.id] = update.to_dict()

    def get_update(self, update_id: str) -> Update | None:
        raw = self.data["archive"].get(str(update_id))
        return Update.from_dict(raw) if raw else None

    def create_session(self, session_id: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        self.data["sessions"][session_id] = payload

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.data["sessions"].get(session_id)

    def save_draft(self, draft: Draft) -> None:
        self.data["drafts"][draft.id] = draft.to_dict()

    def get_draft(self, draft_id: str) -> Draft | None:
        raw = self.data["drafts"].get(draft_id)
        return Draft.from_dict(raw) if raw else None

    def set_awaiting(self, user_id: int, kind: str) -> None:
        self.data["awaiting"][str(user_id)] = kind

    def pop_awaiting(self, user_id: int) -> str:
        return str(self.data["awaiting"].pop(str(user_id), ""))

    def queue_updates(self, updates: list[Update], *, force: bool = False) -> None:
        existing = {str(item.get("id")) for item in self.data["pending_delivery"]}
        for update in updates:
            if update.id in existing:
                continue
            item = update.to_dict()
            item["force"] = force
            self.data["pending_delivery"].append(item)
            existing.add(update.id)

    def pop_pending(self, limit: int) -> list[tuple[Update, bool]]:
        raw = self.data["pending_delivery"][:limit]
        del self.data["pending_delivery"][:limit]
        result: list[tuple[Update, bool]] = []
        for item in raw:
            payload = dict(item)
            force = bool(payload.pop("force", False))
            result.append((Update.from_dict(payload), force))
        return result

    def prune(self) -> None:
        now = datetime.now(timezone.utc)
        seen_cutoff = now - timedelta(days=45)
        session_cutoff = now - timedelta(days=2)
        self.data["seen"] = {
            key: value
            for key, value in self.data.get("seen", {}).items()
            if _parse_dt(value) >= seen_cutoff
        }
        self.data["sessions"] = {
            key: value
            for key, value in self.data.get("sessions", {}).items()
            if _parse_dt(value.get("created_at", "")) >= session_cutoff
        }
        archive = self.data.get("archive", {})
        if len(archive) > 30000:
            ordered = sorted(
                archive.items(),
                key=lambda pair: str(pair[1].get("created_at", "")),
                reverse=True,
            )[:30000]
            self.data["archive"] = dict(ordered)
        drafts = self.data.get("drafts", {})
        if len(drafts) > 3000:
            ordered = sorted(
                drafts.items(),
                key=lambda pair: str(pair[1].get("created_at", "")),
                reverse=True,
            )[:3000]
            self.data["drafts"] = dict(ordered)


def _parse_dt(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
