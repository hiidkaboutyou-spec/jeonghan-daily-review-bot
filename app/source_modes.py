"""Typed, deterministic source-mode filtering for configured X accounts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, TypeVar


class SourceMode(str, Enum):
    """How posts from a configured source enter the review pipeline."""

    FULL_FEED = "full_feed"
    KEYWORD_FILTER = "keyword_filter"


JEONGHAN_KEYWORDS = (
    "Yoon Jeonghan",
    "jeonghan",
    "Hannie",
    "Hanie",
    "정한",
)
JEONGHAN_EMOJIS = ("🪽", "😇", "👼🏻", "👼")


def _keyword_pattern(keywords: Iterable[str]) -> re.Pattern[str]:
    values = sorted({str(value).strip() for value in keywords if str(value).strip()}, key=len, reverse=True)
    if not values:
        return re.compile(r"(?!)")
    alternatives: list[str] = []
    for value in values:
        escaped = re.escape(value)
        if value.isascii() and value[0].isalnum() and value[-1].isalnum():
            alternatives.append(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])")
        else:
            alternatives.append(escaped)
    return re.compile("|".join(alternatives), re.IGNORECASE | re.UNICODE)


def _bool_value(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        value = raw.strip().casefold()
        if value in {"true", "1", "yes", "on"}:
            return True
        if value in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"Expected a boolean value, got {raw!r}")


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Normalized runtime view of one configured source."""

    handle: str
    label: str
    enabled: bool = True
    priority: int = 100
    include_replies: bool = True
    mode: SourceMode = SourceMode.FULL_FEED
    keywords: tuple[str, ...] = JEONGHAN_KEYWORDS
    emojis: tuple[str, ...] = JEONGHAN_EMOJIS

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "SourceConfig":
        if not isinstance(raw, Mapping):
            raise ValueError("Source configuration must be an object")
        handle = str(raw.get("handle", "")).lstrip("@").strip().lower()
        label = str(raw.get("label", "")).strip() or handle
        mode_raw = str(raw.get("mode", "")).strip().lower()
        if mode_raw:
            try:
                mode = SourceMode(mode_raw)
            except ValueError as exc:
                raise ValueError(f"Unknown source mode for @{handle or '?'}: {mode_raw}") from exc
        else:
            # Before modes existed every configured author was accepted in full by
            # the configured-source authority boundary. Preserve that behavior for
            # old state/tests; production config declares every mode explicitly.
            mode = SourceMode.FULL_FEED
        try:
            priority = int(raw.get("priority", 100))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid source priority for @{handle or '?'}") from exc

        custom_keywords = raw.get("keywords")
        if custom_keywords is None:
            keywords = JEONGHAN_KEYWORDS
        elif isinstance(custom_keywords, list):
            keywords = tuple(str(value).strip() for value in custom_keywords if str(value).strip())
        else:
            raise ValueError(f"Source keywords for @{handle or '?'} must be a list")
        custom_emojis = raw.get("emojis")
        if custom_emojis is None:
            emojis = JEONGHAN_EMOJIS
        elif isinstance(custom_emojis, list):
            emojis = tuple(str(value).strip() for value in custom_emojis if str(value).strip())
        else:
            raise ValueError(f"Source emojis for @{handle or '?'} must be a list")
        return cls(
            handle=handle,
            label=label,
            enabled=_bool_value(raw.get("enabled"), default=True),
            priority=priority,
            include_replies=_bool_value(raw.get("include_replies"), default=True),
            mode=mode,
            keywords=keywords,
            emojis=emojis,
        )

    from_dict = from_mapping

    def to_mapping(self) -> dict[str, Any]:
        """Return the legacy dict shape used by existing runtime consumers."""
        return {
            "handle": self.handle,
            "label": self.label,
            "enabled": self.enabled,
            "priority": self.priority,
            "include_replies": self.include_replies,
            "mode": self.mode.value,
            "keywords": list(self.keywords),
            "emojis": list(self.emojis),
        }


class ContentFilter:
    """Match only the explicitly configured Jeonghan names or angel markers."""

    def __init__(
        self,
        keywords: Iterable[str] = JEONGHAN_KEYWORDS,
        emojis: Iterable[str] = JEONGHAN_EMOJIS,
    ) -> None:
        self.keywords = tuple(keywords)
        self.emojis = tuple(dict.fromkeys(str(value) for value in emojis if str(value)))
        self._pattern = _keyword_pattern(self.keywords)

    def matches(self, text: str | None) -> bool:
        value = str(text or "")
        return bool(self._pattern.search(value)) or any(emoji in value for emoji in self.emojis)


class _Post(Protocol):
    author: str
    text: str
    quoted_text: str


PostT = TypeVar("PostT", bound=_Post)


class SourceModeGate:
    """Fail closed for unknown sources and enforce each configured source mode."""

    def __init__(self, sources: Iterable[SourceConfig | Mapping[str, Any]]) -> None:
        normalized = [
            source if isinstance(source, SourceConfig) else SourceConfig.from_mapping(source)
            for source in sources
        ]
        self.sources = {source.handle.casefold(): source for source in normalized if source.handle}
        self.filters = {
            source.handle.casefold(): ContentFilter(source.keywords, source.emojis)
            for source in normalized
            if source.handle and source.mode is SourceMode.KEYWORD_FILTER
        }

    def should_accept_post(self, author_handle: str, text: str | None, quoted_text: str | None = "") -> bool:
        handle = str(author_handle or "").lstrip("@").strip().casefold()
        source = self.sources.get(handle)
        if source is None or not source.enabled:
            return False
        if source.mode is SourceMode.FULL_FEED:
            return True
        content = "\n".join(value for value in (str(text or ""), str(quoted_text or "")) if value)
        return self.filters[handle].matches(content)

    def filter_posts(self, posts: Iterable[PostT]) -> list[PostT]:
        return [
            post
            for post in posts
            if self.should_accept_post(
                str(getattr(post, "author", "") or ""),
                str(getattr(post, "text", "") or ""),
                str(getattr(post, "quoted_text", "") or ""),
            )
        ]
