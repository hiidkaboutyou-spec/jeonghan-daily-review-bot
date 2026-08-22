from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


JEONGHAN_TERMS = {
    "jeonghan",
    "hannie",
    "hanie",
    "hani",
    "yoon jeonghan",
    "yoonjeonghan",
    "정한",
    "윤정한",
    "#jeonghan",
    "#윤정한",
    "#정한",
}

JEONGHAN_EMOJIS = {"🪽", "😇", "👼🏻", "👼"}


VALID_SOURCE_MODES = {"full_feed", "keyword_filter"}


@dataclass(slots=True)
class SourceConfig:
    """Runtime source mode configuration model.
    
    Each configured X source is converted from JSON to this model at startup.
    
    Attributes:
        handle: X handle (without @), normalized at config load.
        mode: Source collection mode. One of: full_feed, keyword_filter.
              Missing or invalid modes default to full_feed.
        enabled: Whether this source is active. Defaults to True.
        include_replies: Whether to include replies. Defaults to True.
        priority: Collection priority (lower = higher). Defaults to 100.
        jeonghan_only: Legacy trusted-source flag. Defaults to False.
    """
    handle: str
    mode: str = "full_feed"
    enabled: bool = True
    include_replies: bool = True
    priority: int = 100
    jeonghan_only: bool = False

    def __post_init__(self) -> None:
        """Normalize mode; store normalized value."""
        if self.mode.strip().lower() not in VALID_SOURCE_MODES:
            self.mode = "full_feed"
        else:
            self.mode = self.mode.strip().lower()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        """Convert JSON dict to SourceConfig, normalizing legacy configs.
        
        Missing mode → defaults to "full_feed" (backward compatible).
        Invalid mode → defaults to "full_feed".
        """
        return cls(
            handle=str(data.get("handle", "")).strip(),
            mode=str(data.get("mode", "full_feed")).strip(),
            enabled=bool(data.get("enabled", True)),
            include_replies=bool(data.get("include_replies", True)),
            priority=int(data.get("priority", 100)),
            jeonghan_only=bool(data.get("jeonghan_only", False)),
        )


def normalize_source_mode(source: dict[str, Any]) -> str:
    """Normalize source mode from raw dict (legacy).
    
    Kept for backward compatibility with code that hasn't migrated to SourceConfig.
    """
    mode = str(source.get("mode", "full_feed")).strip().lower()
    return mode if mode in VALID_SOURCE_MODES else "full_feed"


def matches_jeonghan_filter(text: str) -> bool:
    """Check if text contains Jeonghan keywords, hashtags, or emojis."""
    value = str(text or "")
    folded = value.casefold()
    return any(term.casefold() in folded for term in JEONGHAN_TERMS) or any(
        emoji in value for emoji in JEONGHAN_EMOJIS
    )


def should_collect_post(source: SourceConfig | dict[str, Any], text: str) -> bool:
    """Decide whether a post from a source should be collected.
    
    Args:
        source: SourceConfig instance or legacy dict.
        text: Post text to evaluate.
    
    Returns:
        True if post matches source mode rules; False to reject.
    """
    if isinstance(source, SourceConfig):
        mode = source.mode
    else:
        mode = normalize_source_mode(source)
    
    if mode == "full_feed":
        return True
    return matches_jeonghan_filter(text)


def filter_posts_for_source_mode(source: SourceConfig | dict[str, Any], posts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter a list of posts by source mode rules.
    
    Args:
        source: SourceConfig instance or legacy dict.
        posts: Iterable of post dicts with at least a 'text' field.
    
    Returns:
        List of posts that pass source mode filtering.
    """
    return [
        post
        for post in posts
        if should_collect_post(source, str(post.get("text", "")))
    ]
