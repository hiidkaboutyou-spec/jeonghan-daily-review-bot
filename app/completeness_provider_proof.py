"""Provider-structure proof for Phase 4 completeness.

The pinned twscrape parser intentionally walks every Tweet object in a raw GraphQL
response. That is useful for extraction, but nested/self-quoted or pinned tweets are
not safe pagination-boundary witnesses. This module keeps extraction unchanged and
adds a proof-only view of top-level UserTweets timeline entries.
"""
from __future__ import annotations

from contextlib import aclosing
from datetime import datetime, timezone
from typing import Any

from . import phase3_recovery as recovery
from .completeness_evidence import active_evidence


def _post_id(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    value = str(entry.get("entryId", "") or "")
    if value.startswith("tweet-"):
        return value[len("tweet-") :].strip()
    return ""


def _instruction_lists(value: Any):
    if isinstance(value, dict):
        instructions = value.get("instructions")
        if isinstance(instructions, list) and any(
            isinstance(item, dict)
            and str(item.get("type", "")).startswith("Timeline")
            for item in instructions
        ):
            yield instructions
        for child in value.values():
            yield from _instruction_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _instruction_lists(child)


def _timeline_structure(
    payload: Any,
) -> tuple[list[dict[str, Any]], list[str], set[str], bool]:
    """Return timeline instructions, ordered normal IDs, pinned IDs, bottom termination."""
    instructions: list[dict[str, Any]] = []
    for group in _instruction_lists(payload):
        instructions.extend(item for item in group if isinstance(item, dict))

    pinned_ids: set[str] = set()
    terminated_bottom = False
    for instruction in instructions:
        kind = str(instruction.get("type", "") or "")
        if kind == "TimelinePinEntry":
            post_id = _post_id(instruction.get("entry"))
            if post_id:
                pinned_ids.add(post_id)
        elif (
            kind == "TimelineTerminateTimeline"
            and str(instruction.get("direction", "")) == "Bottom"
        ):
            terminated_bottom = True

    ordered_ids: list[str] = []
    for instruction in instructions:
        if str(instruction.get("type", "") or "") != "TimelineAddEntries":
            continue
        entries = instruction.get("entries", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            post_id = _post_id(entry)
            if post_id and post_id not in pinned_ids:
                ordered_ids.append(post_id)
    return instructions, ordered_ids, pinned_ids, terminated_bottom


def _tweet_id(tweet: Any) -> str:
    return str(
        getattr(tweet, "id_str", "") or getattr(tweet, "id", "") or ""
    ).strip()


def _tweet_author(tweet: Any) -> str:
    user = getattr(tweet, "user", None)
    return (
        str(getattr(user, "username", "") or "")
        .lstrip("@")
        .strip()
        .casefold()
    )


def _tweet_time(tweet: Any) -> datetime | None:
    value = getattr(tweet, "date", None)
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_bound(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _record_structural_proof(
    *,
    tweets: list[Any],
    ordered_ids: list[str],
    pinned_ids: set[str],
) -> bool:
    """Attach safe page expectations to the active attempt.

    COMPLETE lower-bound proof needs ordering across the whole traversal, not merely
    within each page. Cursor pagination may overlap IDs, so cross-page ordering is
    checked only across newly observed top-level IDs.
    """
    evidence = active_evidence.get()
    if evidence is None:
        return True

    source = str(evidence.source_handle or "").lstrip("@").strip().casefold()
    start = _parse_bound(evidence.window_start)
    end = _parse_bound(evidence.window_end)
    if not source or start is None or end is None or start >= end:
        return False

    by_id = {_tweet_id(tweet): tweet for tweet in tweets if _tweet_id(tweet)}
    structure_valid = True
    ordered_pairs: list[tuple[str, datetime]] = []

    for post_id in ordered_ids:
        tweet = by_id.get(post_id)
        if tweet is None or _tweet_author(tweet) != source:
            structure_valid = False
            continue
        created = _tweet_time(tweet)
        if created is None:
            structure_valid = False
            continue
        ordered_pairs.append((post_id, created))
        if start <= created < end:
            evidence.expected_window_ids.add(post_id)

    for post_id in pinned_ids:
        tweet = by_id.get(post_id)
        if tweet is None or _tweet_author(tweet) != source:
            continue
        created = _tweet_time(tweet)
        if created is not None and start <= created < end:
            evidence.expected_window_ids.add(post_id)

    ordered_times = [created for _, created in ordered_pairs]
    within_page_monotonic = all(
        left >= right for left, right in zip(ordered_times, ordered_times[1:])
    )
    if ordered_times and not within_page_monotonic:
        evidence.timeline_order_valid = False
        structure_valid = False

    new_pairs = [
        (post_id, created)
        for post_id, created in ordered_pairs
        if post_id not in evidence.top_level_ids_seen
    ]
    previous_oldest = _parse_bound(evidence.previous_page_oldest_at)
    if within_page_monotonic and new_pairs and previous_oldest is not None:
        next_newest = new_pairs[0][1]
        if next_newest > previous_oldest:
            evidence.timeline_order_valid = False
            structure_valid = False

    if ordered_pairs:
        newest = max(created for _, created in ordered_pairs)
        oldest = min(created for _, created in ordered_pairs)
        prior_newest = _parse_bound(evidence.newest_top_level_at)
        prior_oldest = _parse_bound(evidence.oldest_top_level_at)
        if prior_newest is None or newest > prior_newest:
            evidence.newest_top_level_at = _iso(newest)
        if prior_oldest is None or oldest < prior_oldest:
            evidence.oldest_top_level_at = _iso(oldest)

    if within_page_monotonic and new_pairs:
        evidence.previous_page_oldest_at = _iso(new_pairs[-1][1])
    evidence.top_level_ids_seen.update(post_id for post_id, _ in ordered_pairs)

    if (
        ordered_times
        and within_page_monotonic
        and evidence.timeline_order_valid
        and any(created < start for created in ordered_times)
    ):
        evidence.lower_boundary_proven = True

    return structure_valid


async def _provider_page(
    api: Any,
    user_id: int,
    *,
    include_replies: bool,
    cursor: str | None,
):
    method_name = (
        "user_tweets_and_replies_raw" if include_replies else "user_tweets_raw"
    )
    method = getattr(api, method_name, None)
    if not callable(method):
        raise AttributeError(method_name)

    kwargs: dict[str, Any] = {"limit": 1}
    if cursor:
        kwargs["kv"] = {"cursor": cursor}

    response = None
    generator = method(user_id, **kwargs)
    async with aclosing(generator) as stream:
        async for item in stream:
            response = item
            break

    if response is None:
        return recovery._ProviderPage([], None, True, False)

    payload = response.json()
    from twscrape.models import parse_tweets

    tweets = list(parse_tweets(payload, -1))
    get_cursor = getattr(api, "_get_cursor", None)
    if not callable(get_cursor):
        raise RuntimeError("provider cursor helper unavailable")
    next_cursor = get_cursor(payload, "Bottom")

    instructions, ordered_ids, pinned_ids, terminated_bottom = _timeline_structure(
        payload
    )
    has_add_entries = any(
        str(item.get("type", "") or "") == "TimelineAddEntries"
        for item in instructions
    )
    valid = bool(
        isinstance(payload, dict)
        and not payload.get("errors")
        and (has_add_entries or terminated_bottom)
    )
    if valid:
        valid = _record_structural_proof(
            tweets=tweets,
            ordered_ids=ordered_ids,
            pinned_ids=pinned_ids,
        )

    exhausted = bool(valid and (terminated_bottom or not next_cursor))
    return recovery._ProviderPage(
        tweets=tweets,
        next_cursor=str(next_cursor) if next_cursor else None,
        exhausted=exhausted,
        valid_response=valid,
    )


def install() -> None:
    current = recovery._provider_page
    if getattr(current, "_phase4_provider_proof", False):
        return
    _provider_page._phase4_provider_proof = True
    recovery._provider_page = _provider_page


install()
