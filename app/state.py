from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import Draft, Update

SCHEMA_VERSION = 3


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _fresh(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "telegram_offset": 0,
            "telegram_failures": {},
            "last_auto_run": "",
            "last_x_error_notice": "",
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
        if not isinstance(value, dict):
            return self._fresh()
        return self._normalize_loaded(value)

    def _normalize_loaded(self, value: dict[str, Any]) -> dict[str, Any]:
        """Migrate older/partial state without trusting its nested types."""
        fresh = self._fresh()
        try:
            fresh["telegram_offset"] = max(0, int(value.get("telegram_offset", 0) or 0))
        except (TypeError, ValueError):
            fresh["telegram_offset"] = 0
        for key in ("last_auto_run", "last_x_error_notice"):
            raw = value.get(key, "")
            fresh[key] = str(raw) if isinstance(raw, (str, int, float)) else ""
        for key in ("seen", "archive", "sessions", "drafts", "awaiting"):
            raw = value.get(key)
            if isinstance(raw, dict):
                fresh[key] = raw
        failures = value.get("telegram_failures")
        if isinstance(failures, dict):
            clean: dict[str, dict[str, Any]] = {}
            for key, raw in failures.items():
                if not isinstance(raw, dict):
                    continue
                try:
                    count = max(0, min(int(raw.get("count", 0) or 0), 100))
                except (TypeError, ValueError):
                    count = 0
                clean[str(key)] = {
                    "count": count,
                    "last_failure_at": str(raw.get("last_failure_at", ""))[:80],
                    "error_type": str(raw.get("error_type", ""))[:80],
                }
            fresh["telegram_failures"] = clean
        pending = value.get("pending_delivery")
        if isinstance(pending, list):
            fresh["pending_delivery"] = [item for item in pending if isinstance(item, dict)]
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

    def record_telegram_failure(self, update_id: int, error_type: str) -> int:
        """Record only bounded technical metadata; never store exception messages/secrets."""
        key = str(int(update_id))
        current = self.data.setdefault("telegram_failures", {}).get(key, {})
        try:
            count = int(current.get("count", 0) or 0) + 1
        except (TypeError, ValueError):
            count = 1
        self.data["telegram_failures"][key] = {
            "count": min(count, 100),
            "last_failure_at": datetime.now(timezone.utc).isoformat(),
            "error_type": str(error_type or "Error")[:80],
        }
        return count

    def clear_telegram_failure(self, update_id: int) -> None:
        self.data.setdefault("telegram_failures", {}).pop(str(int(update_id)), None)

    def telegram_failure_count(self, update_id: int) -> int:
        raw = self.data.setdefault("telegram_failures", {}).get(str(int(update_id)), {})
        try:
            return max(0, int(raw.get("count", 0) or 0))
        except (AttributeError, TypeError, ValueError):
            return 0

    def mark_seen(self, update: Update) -> None:
        self.data["seen"][update.id] = datetime.now(timezone.utc).isoformat()
        self.data["archive"][update.id] = update.to_dict()

    def is_seen(self, update_id: str) -> bool:
        return str(update_id) in self.data["seen"]

    def archive_update(self, update: Update) -> None:
        self.data["archive"][update.id] = update.to_dict()

    def get_update(self, update_id: str) -> Update | None:
        raw = self.data["archive"].get(str(update_id))
        if not isinstance(raw, dict):
            return None
        try:
            return Update.from_dict(raw)
        except (TypeError, ValueError):
            return None

    def create_session(self, session_id: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        self.data["sessions"][session_id] = payload

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        value = self.data["sessions"].get(session_id)
        return value if isinstance(value, dict) else None

    def save_draft(self, draft: Draft) -> None:
        self.data["drafts"][draft.id] = draft.to_dict()

    def get_draft(self, draft_id: str) -> Draft | None:
        raw = self.data["drafts"].get(draft_id)
        if not isinstance(raw, dict):
            return None
        try:
            return Draft.from_dict(raw)
        except TypeError:
            return None

    def set_awaiting(self, user_id: int, kind: str) -> None:
        self.data["awaiting"][str(user_id)] = kind

    def pop_awaiting(self, user_id: int) -> str:
        return str(self.data["awaiting"].pop(str(user_id), ""))

    def queue_updates(self, updates: list[Update], *, force: bool = False) -> None:
        existing = {str(item.get("id")) for item in self.data["pending_delivery"] if isinstance(item, dict)}
        for update in updates:
            if update.id in existing:
                continue
            item = update.to_dict()
            item["force"] = force
            self.data["pending_delivery"].append(item)
            existing.add(update.id)

    def pop_pending(self, limit: int) -> list[tuple[Update, bool]]:
        """Peek pending items; remove only entries already marked seen."""
        queue = list(self.data.get("pending_delivery", []))
        remaining: list[dict[str, Any]] = []
        result: list[tuple[Update, bool]] = []
        limit = max(0, int(limit))
        for item in queue:
            if not isinstance(item, dict):
                continue
            update_id = str(item.get("id", ""))
            if update_id and self.is_seen(update_id):
                continue
            payload = dict(item)
            force = bool(payload.pop("force", False))
            try:
                update = Update.from_dict(payload)
            except (TypeError, ValueError):
                # Drop poison entries before applying the delivery limit. Otherwise
                # one malformed first row can starve every valid update behind it.
                continue
            remaining.append(item)
            if len(result) < limit:
                result.append((update, force))
        self.data["pending_delivery"] = remaining
        return result

    def prune(self) -> None:
        now = datetime.now(timezone.utc)
        seen_cutoff = now - timedelta(days=45)
        session_cutoff = now - timedelta(days=2)
        failure_cutoff = now - timedelta(days=2)
        self.data["seen"] = {
            key: value
            for key, value in self.data.get("seen", {}).items()
            if _parse_dt(value) >= seen_cutoff
        }
        self.data["sessions"] = {
            key: value
            for key, value in self.data.get("sessions", {}).items()
            if isinstance(value, dict) and _parse_dt(value.get("created_at", "")) >= session_cutoff
        }
        self.data["telegram_failures"] = {
            key: value
            for key, value in self.data.get("telegram_failures", {}).items()
            if isinstance(value, dict)
            and _parse_dt(value.get("last_failure_at", "")) >= failure_cutoff
        }
        archive = self.data.get("archive", {})
        if isinstance(archive, dict) and len(archive) > 30000:
            ordered = sorted(
                ((k, v) for k, v in archive.items() if isinstance(v, dict)),
                key=lambda pair: str(pair[1].get("created_at", "")),
                reverse=True,
            )[:30000]
            self.data["archive"] = dict(ordered)
        drafts = self.data.get("drafts", {})
        if isinstance(drafts, dict) and len(drafts) > 3000:
            ordered = sorted(
                ((k, v) for k, v in drafts.items() if isinstance(v, dict)),
                key=lambda pair: str(pair[1].get("created_at", "")),
                reverse=True,
            )[:3000]
            self.data["drafts"] = dict(ordered)


def _parse_dt(value: Any) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
