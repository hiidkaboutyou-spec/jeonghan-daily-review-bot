"""Runtime source mode model and filtering for Jeonghan content.

Supports two collection modes:
1. full_feed: Trust the source completely, collect all posts
2. keyword_filter: Collect only Jeonghan-related posts based on keywords/emojis
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class SourceMode(str, Enum):
    """Source filtering behavior mode."""
    FULL_FEED = "full_feed"
    KEYWORD_FILTER = "keyword_filter"


# Jeonghan keyword patterns (case-insensitive matches)
JEONGHAN_KEYWORDS = {
    "JEONGHAN",
    "Jeonghan",
    "jeonghan",
    "Hannie",
    "Hanie",
    "Hani",
    "정한",
    "윤정한",
    "Yoon Jeonghan",
    "Yoonjeonghan",
    "#JEONGHAN",
    "#윤정한",
    "#정한",
}

# Jeonghan angel emoji markers
JEONGHAN_EMOJIS = {
    "🪽",  # wing
    "😇",  # angel face
    "👼🏻",  # baby angel with light skin tone
    "👼",  # baby angel
}


def _normalize_keyword(keyword: str) -> str:
    """Normalize keyword for comparison."""
    return str(keyword or "").strip()


def _build_keyword_pattern() -> re.Pattern[str]:
    """Build regex pattern for keyword matching."""
    escaped_keywords = [re.escape(kw) for kw in sorted(JEONGHAN_KEYWORDS, key=len, reverse=True)]
    pattern_str = "|".join(escaped_keywords)
    return re.compile(pattern_str, re.IGNORECASE | re.UNICODE)


_KEYWORD_PATTERN = _build_keyword_pattern()


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Runtime configuration for a single source.
    
    Attributes:
        handle: X account handle (normalized, without @)
        label: Human-readable label for the source
        enabled: Whether to collect from this source
        priority: Priority level for sorting (higher = more important)
        include_replies: Whether to include replies from this source
        mode: Filtering mode (full_feed or keyword_filter)
    """
    
    handle: str
    label: str
    enabled: bool
    priority: int
    include_replies: bool
    mode: SourceMode
    keywords: tuple[str, ...] = ()
    emojis: tuple[str, ...] = ()
    
    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> SourceConfig:
        """Create SourceConfig from JSON mapping.
        
        Supports legacy format with 'jeonghan_only' for backward compatibility.
        """
        handle = str(raw.get("handle", "")).strip().lstrip("@").lower()
        label = str(raw.get("label", handle)).strip() or handle
        enabled = bool(raw.get("enabled", True))
        priority = int(raw.get("priority", 100))
        include_replies = bool(raw.get("include_replies", True))
        
        # Determine mode: prioritize explicit mode, fall back to jeonghan_only
        mode_str = str(raw.get("mode", "")).strip().lower()
        if mode_str in {"full_feed", "keyword_filter"}:
            mode = SourceMode(mode_str)
        elif raw.get("jeonghan_only", False):
            # Legacy format: jeonghan_only=true means keyword_filter
            mode = SourceMode.KEYWORD_FILTER
        else:
            # Default: trusted sources get full_feed
            mode = SourceMode.FULL_FEED
        
        # Extract keywords and emojis
        keywords = tuple(
            _normalize_keyword(kw) 
            for kw in raw.get("keywords", []) 
            if _normalize_keyword(kw)
        )
        emojis = tuple(
            str(emoji).strip() 
            for emoji in raw.get("emojis", []) 
            if str(emoji).strip()
        )
        
        return cls(
            handle=handle,
            label=label,
            enabled=enabled,
            priority=priority,
            include_replies=include_replies,
            mode=mode,
            keywords=keywords or JEONGHAN_KEYWORDS,
            emojis=emojis or JEONGHAN_EMOJIS,
        )


class ContentFilter:
    """Filter posts based on keywords and emojis."""
    
    def __init__(
        self,
        keywords: set[str] | None = None,
        emojis: set[str] | None = None,
    ):
        """Initialize content filter.
        
        Args:
            keywords: Set of keywords to match (case-insensitive). Defaults to JEONGHAN_KEYWORDS.
            emojis: Set of emojis to match. Defaults to JEONGHAN_EMOJIS.
        """
        self.keywords = keywords or JEONGHAN_KEYWORDS
        self.emojis = emojis or JEONGHAN_EMOJIS
        self._pattern = _KEYWORD_PATTERN if keywords is None else self._build_pattern()
    
    def _build_pattern(self) -> re.Pattern[str]:
        """Build regex pattern from custom keywords."""
        if not self.keywords:
            return re.compile(r"(?!)")  # Never matches
        escaped = [re.escape(kw) for kw in sorted(self.keywords, key=len, reverse=True)]
        pattern_str = "|".join(escaped)
        return re.compile(pattern_str, re.IGNORECASE | re.UNICODE)
    
    def matches(self, text: str) -> bool:
        """Check if text contains keywords.
        
        Returns:
            True if any keyword is found in the text.
        """
        return bool(self._pattern.search(text))
    
    def has_emoji(self, text: str) -> bool:
        """Check if text contains any filter emojis.
        
        Returns:
            True if any emoji is found in the text.
        """
        return any(emoji in text for emoji in self.emojis)
    
    def passes(self, text: str) -> bool:
        """Check if post passes content filter (keyword OR emoji match).
        
        Returns:
            True if text has keyword match or emoji match.
        """
        return self.matches(text) or self.has_emoji(text)


class SourceModeGate:
    """Apply source mode filtering to posts."""
    
    def __init__(self, sources: list[SourceConfig] | None = None):
        """Initialize gate with sources.
        
        Args:
            sources: List of SourceConfig objects. If None, creates empty gate.
        """
        self.sources = {src.handle: src for src in (sources or []) if src.handle and src.enabled}
        self.filters: dict[str, ContentFilter] = {}
        for src in self.sources.values():
            if src.mode == SourceMode.KEYWORD_FILTER:
                self.filters[src.handle] = ContentFilter(
                    keywords=set(src.keywords) if src.keywords else None,
                    emojis=set(src.emojis) if src.emojis else None,
                )
    
    def should_accept_post(self, author_handle: str, text: str) -> bool:
        """Determine if post should be accepted based on source mode.
        
        Args:
            author_handle: Normalized X handle (without @)
            text: Post content text
            
        Returns:
            True if post should be accepted, False if filtered out.
        """
        handle = str(author_handle or "").strip().lstrip("@").lower()
        
        # Unknown source: reject (not in configured sources)
        if handle not in self.sources:
            logger.debug("Post from unconfigured source @%s rejected", handle)
            return False
        
        config = self.sources[handle]
        
        # Disabled source: reject
        if not config.enabled:
            logger.debug("Post from disabled source @%s rejected", handle)
            return False
        
        # Full feed mode: accept all posts from this source
        if config.mode == SourceMode.FULL_FEED:
            logger.debug("Post from @%s accepted (full_feed mode)", handle)
            return True
        
        # Keyword filter mode: check content
        if config.mode == SourceMode.KEYWORD_FILTER:
            filter_obj = self.filters.get(handle)
            if filter_obj is None:
                logger.warning("No filter configured for @%s in keyword_filter mode", handle)
                return False
            
            if filter_obj.passes(text):
                logger.debug("Post from @%s accepted (matches keyword_filter)", handle)
                return True
            else:
                logger.debug("Post from @%s rejected (doesn't match keyword_filter)", handle)
                return False
        
        # Unknown mode: safe default is reject
        logger.warning("Unknown source mode %s for @%s", config.mode, handle)
        return False
    
    def filter_posts(self, posts: list[Any]) -> list[Any]:
        """Filter a list of posts based on source modes.
        
        Posts must have 'author' and 'text' attributes.
        
        Args:
            posts: List of post objects
            
        Returns:
            Filtered list of accepted posts.
        """
        result = []
        for post in posts:
            try:
                author = getattr(post, "author", None) or ""
                text = getattr(post, "text", None) or ""
                if self.should_accept_post(str(author), str(text)):
                    result.append(post)
            except Exception as exc:
                logger.warning("Error filtering post: %s", exc, exc_info=True)
        return result


def load_source_modes_from_config(config: dict[str, Any]) -> tuple[list[SourceConfig], SourceModeGate]:
    """Load source modes from configuration dictionary.
    
    Args:
        config: Configuration dict with 'sources' key
        
    Returns:
        Tuple of (list of SourceConfig, SourceModeGate instance)
    """
    sources_raw = config.get("sources", [])
    source_configs = [
        SourceConfig.from_mapping(source_raw)
        for source_raw in sources_raw
        if isinstance(source_raw, dict)
    ]
    
    logger.info("Loaded %d source configuration(s)", len(source_configs))
    for src in source_configs:
        logger.info(
            "Source @%s: mode=%s, enabled=%s, priority=%d",
            src.handle, src.mode.value, src.enabled, src.priority
        )
    
    gate = SourceModeGate(source_configs)
    return source_configs, gate
