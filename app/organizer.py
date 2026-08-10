from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import timedelta

from .models import EventGroup, Update

LIVE_WORDS = (
    "live",
    "weverse",
    "라이브",
    "위버스",
    "لایو",
    "ترجمه",
    "생방",
)
INSTAGRAM_WORDS = ("instagram", "insta", "ig update", "인스타", "اینستا", "reel", "story")
BRAND_WORDS = ("campaign", "brand", "banila", "ambassador", "برند", "광고")
BRAND_RE = re.compile(r"(?<![\w#])(?:ad|sponsored)(?![\w-])", re.I)
FANSIGN_WORDS = ("fansign", "fan sign", "fancall", "fan call", "영통", "팬싸", "فن کال", "فنساین")
AIRPORT_WORDS = ("airport", "incheon", "gimpo", "공항", "فرودگاه")
JEONGHAN_OWN_HANDLES = {"jeonghaniyoo_n", "jeonghan", "yoonjeonghan"}


def _stable_id_key(value: str) -> tuple[int, int | str]:
    """Comparable deterministic key for numeric and synthetic/non-numeric IDs."""
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def detect_category(update: Update) -> str:
    text = f"{update.text} {update.quoted_text} {update.author}".lower()
    if any(word in text for word in LIVE_WORDS):
        return "live"
    if any(word in text for word in INSTAGRAM_WORDS):
        if update.author.lower() in JEONGHAN_OWN_HANDLES or "jeonghan instagram" in text:
            return "jeonghan_instagram"
        return "member_instagram"
    if any(word in text for word in BRAND_WORDS) or BRAND_RE.search(text):
        return "brand"
    if any(word in text for word in FANSIGN_WORDS):
        return "fansign"
    if any(word in text for word in AIRPORT_WORDS):
        return "airport"
    return "general"


def extract_part_number(text: str) -> int | None:
    patterns = (
        r"(?:part|pt\.?|بخش|پارت)\s*[-:#]?\s*(\d{1,3})",
        r"\b(\d{1,3})\s*/\s*\d{1,3}\b",
        r"(?:ep\.?|episode)\s*(\d{1,3})",
    )
    lowered = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def normalized_event_tokens(text: str) -> str:
    value = re.sub(r"https?://\S+", " ", text.lower())
    value = re.sub(r"[@#][\w_]+", " ", value)
    value = re.sub(r"[^0-9a-z\u0600-\u06ff\u3040-\u30ff\uac00-\ud7af]+", " ", value)
    stop = {
        "jeonghan",
        "정한",
        "윤정한",
        "ジョンハン",
        "جونگهان",
        "هانی",
        "update",
        "اپدیت",
        "آپدیت",
        "the",
        "and",
        "with",
    }
    tokens = [token for token in value.split() if token not in stop and len(token) > 1]
    return " ".join(tokens[:7])


def fallback_title(category: str, update: Update) -> str:
    labels = {
        "live": "لایو جونگهان",
        "jeonghan_instagram": "اینستاگرام جونگهان",
        "member_instagram": "آپدیت اینستاگرام اعضا با جونگهان",
        "brand": "آپدیت برند با جونگهان",
        "fansign": "فنساین جونگهان",
        "airport": "آپدیت فرودگاه جونگهان",
        "general": "آپدیت جونگهان",
    }
    base = labels.get(category, labels["general"])
    # A raw source-language suffix is not a useful Persian heading and previously
    # leaked fragments such as "to seungcheol" into ready-looking drafts.
    return base


def initial_event_key(update: Update) -> str:
    category = update.category or detect_category(update)
    update.category = category
    if update.conversation_id and update.conversation_id != update.id:
        return f"conversation:{update.conversation_id}"
    if update.reply_to_id:
        return f"conversation:{update.conversation_id or update.reply_to_id}"
    local_day = update.created_at.strftime("%Y-%m-%d")
    if category == "live":
        block = update.created_at.hour // 4
        return f"live:{update.author.lower()}:{local_day}:{block}"
    if category in {"jeonghan_instagram", "member_instagram"}:
        return f"instagram:{update.author.lower()}:{local_day}:{update.conversation_id or update.id}"
    if category in {"brand", "fansign", "airport"}:
        token_key = normalized_event_tokens(update.text)[:40] or update.id
        digest = hashlib.sha1(token_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        return f"{category}:{local_day}:{digest}"
    return f"single:{update.id}"


def deduplicate(updates: list[Update]) -> list[Update]:
    chosen: dict[str, Update] = {}
    for update in updates:
        current = chosen.get(update.id)
        if current is None:
            chosen[update.id] = update
            continue
        current_score = (len(current.media), len(current.text), -current.source_priority)
        new_score = (len(update.media), len(update.text), -update.source_priority)
        if new_score > current_score:
            chosen[update.id] = update
    return list(chosen.values())


def organize_updates(updates: list[Update]) -> list[EventGroup]:
    clean = deduplicate(updates)
    for update in clean:
        update.category = detect_category(update)
        update.event_key = initial_event_key(update)
        if not update.event_title:
            update.event_title = fallback_title(update.category, update)

    # Only cluster standalone live posts. Real X conversation/thread IDs are stronger
    # evidence and must never be overwritten merely because two lives are close in time.
    live_by_author: dict[tuple[str, str], list[Update]] = defaultdict(list)
    for update in clean:
        is_threaded = bool(update.reply_to_id) or (
            bool(update.conversation_id) and update.conversation_id != update.id
        )
        if update.category == "live" and not is_threaded:
            live_by_author[(update.author.lower(), update.created_at.strftime("%Y-%m-%d"))].append(update)
    for items in live_by_author.values():
        items.sort(key=lambda item: (item.created_at, _stable_id_key(item.id)))
        cluster = 0
        previous = None
        for item in items:
            if previous is not None and item.created_at - previous > timedelta(hours=4):
                cluster += 1
            item.event_key = f"live:{item.author.lower()}:{item.created_at:%Y-%m-%d}:{cluster}"
            previous = item.created_at

    grouped: dict[str, list[Update]] = defaultdict(list)
    for update in clean:
        grouped[update.event_key].append(update)

    result: list[EventGroup] = []
    for key, items in grouped.items():
        category = items[0].category
        part_numbers = [extract_part_number(item.text) for item in items]
        use_part_order = category == "live" and len(items) > 1 and all(
            number is not None for number in part_numbers
        )

        def sort_key(item: Update):
            number = extract_part_number(item.text)
            if use_part_order:
                return (
                    number if number is not None else 10_000,
                    item.created_at,
                    _stable_id_key(item.id),
                )
            return (
                item.created_at,
                number if number is not None else 10_000,
                _stable_id_key(item.id),
            )

        items.sort(key=sort_key)
        title = next((item.event_title for item in items if item.event_title), fallback_title(category, items[0]))
        for item in items:
            item.event_key = key
            item.event_title = title
        result.append(EventGroup(key=key, category=category, title=title, updates=items))

    result.sort(key=lambda group: (group.started_at, group.key))
    return result
