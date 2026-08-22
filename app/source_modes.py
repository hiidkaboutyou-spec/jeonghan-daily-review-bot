from __future__ import annotations

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


def normalize_source_mode(source: dict[str, Any]) -> str:
    mode = str(source.get("mode", "full_feed")).strip().lower()
    return mode if mode in VALID_SOURCE_MODES else "full_feed"


def matches_jeonghan_filter(text: str) -> bool:
    value = str(text or "")
    folded = value.casefold()
    return any(term.casefold() in folded for term in JEONGHAN_TERMS) or any(
        emoji in value for emoji in JEONGHAN_EMOJIS
    )


def should_collect_post(source: dict[str, Any], text: str) -> bool:
    if normalize_source_mode(source) == "full_feed":
        return True
    return matches_jeonghan_filter(text)


def filter_posts_for_source_mode(source: dict[str, Any], posts: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        post
        for post in posts
        if should_collect_post(source, str(post.get("text", "")))
    ]
