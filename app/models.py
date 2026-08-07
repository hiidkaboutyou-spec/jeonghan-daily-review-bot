from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def ensure_utc(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif value:
        raw = str(value).strip()
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(raw)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(slots=True)
class MediaItem:
    kind: str
    url: str
    preview_url: str = ""
    bitrate: int = 0
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    content_type: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MediaItem":
        return cls(**{k: value.get(k, f.default) for k, f in cls.__dataclass_fields__.items()})


@dataclass(slots=True)
class Update:
    id: str
    url: str
    author: str
    author_name: str
    text: str
    created_at: datetime
    conversation_id: str = ""
    reply_to_id: str = ""
    quoted_id: str = ""
    lang: str = ""
    media: list[MediaItem] = field(default_factory=list)
    category: str = "general"
    event_key: str = ""
    event_title: str = ""
    source_priority: int = 100
    is_reply: bool = False
    raw_query: str = ""

    def __post_init__(self) -> None:
        self.created_at = ensure_utc(self.created_at)
        self.author = self.author.lstrip("@").strip()
        self.id = str(self.id)
        self.conversation_id = str(self.conversation_id or self.id)
        if not self.url and self.author and self.id:
            self.url = f"https://x.com/{self.author}/status/{self.id}"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Update":
        data = dict(value)
        data["created_at"] = ensure_utc(data.get("created_at"))
        data["media"] = [MediaItem.from_dict(item) for item in data.get("media", [])]
        return cls(**data)


@dataclass(slots=True)
class EventGroup:
    key: str
    category: str
    title: str
    updates: list[Update]

    @property
    def started_at(self) -> datetime:
        return min(item.created_at for item in self.updates)

    @property
    def ended_at(self) -> datetime:
        return max(item.created_at for item in self.updates)


@dataclass(slots=True)
class Draft:
    id: str
    update_id: str
    event_key: str
    caption: str
    mode: str = "default"
    telegram_message_id: int | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Draft":
        return cls(**value)
