from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


RLM = "\u200f"
PERSIAN_RE = re.compile(r"[\u0600-\u06ff]")
LEADING_DECORATION_RE = re.compile(r"^[^\w\u0600-\u06ff]+", re.UNICODE)

DEFAULT_THEMES: dict[str, list[str]] = {
    "live": [
        "،، ᘒ🪁 ִ˒˒ {date} {actor}’s weverse live",
        "،، ♥︎₊ ִ˒˒ {date} {actor}’s weverse live",
        "،، ഒ˚🫟 ִ˒˒ {date} {actor}’s weverse live",
    ],
    "instagram_jeonghan": [
        "،، 🧸 #IG ׂ ✧ ﹫ jeonghaniyoo_n",
    ],
    "instagram_member": [
        "،، ✧ #IG ׂ ﹫ {account}",
        "،، 🤍🌸໑ ִ˒˒ {account}’s instagram",
    ],
    "news": [
        "،، ࣪˖ ⌕ ִ˒˒ {date} HANNIE NEWS",
    ],
    "reminder": [
        "،، ꒱ ریمایندر به زمانی که ♥︎ 🖇️",
    ],
    "photo": [
        "،، 🪽⌕໋ ִ˒˒ {date} HANNIE UPDATE",
    ],
    "video": [
        "،، 🎞️⌕໋ ִ˒˒ {date} HANNIE VIDEO",
    ],
    "general": [
        "،، 🪽⌕໋ ִ˒˒ {date} HANNIE UPDATE",
    ],
}

ACTOR_NAMES = {
    "jeonghan": "jeonghan",
    "hannie": "jeonghan",
    "정한": "jeonghan",
    "جونگهان": "jeonghan",
    "mingyu": "mingyu",
    "민규": "mingyu",
    "مینگیو": "mingyu",
    "seungcheol": "seungcheol",
    "scoups": "seungcheol",
    "cheol": "seungcheol",
    "승철": "seungcheol",
    "سونگچول": "seungcheol",
    "hoshi": "hoshi",
    "호시": "hoshi",
    "هوشی": "hoshi",
    "seungkwan": "seungkwan",
    "승관": "seungkwan",
    "سونگکوان": "seungkwan",
    "dk": "dokyeom",
    "dokyeom": "dokyeom",
    "도겸": "dokyeom",
    "دوکیوم": "dokyeom",
    "joshua": "joshua",
    "조슈아": "joshua",
    "جاشوا": "joshua",
}


def parse_record_date(record: dict[str, Any]) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(record.get("date", "") or ""))
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def local_datetime(record: dict[str, Any], timezone_name: str) -> datetime:
    try:
        target = ZoneInfo(timezone_name)
    except Exception:
        target = timezone.utc
    return parse_record_date(record).astimezone(target)


def local_date_code(record: dict[str, Any], timezone_name: str = "Asia/Tehran") -> str:
    return local_datetime(record, timezone_name).strftime("%y%m%d")


def _find_live_actor(text: str) -> str:
    folded = text.casefold()
    possessive = re.search(
        r"([a-z][a-z0-9_]*)[’']s\s+(?:weverse\s+)?live",
        folded,
    )
    if possessive:
        token = possessive.group(1)
        return ACTOR_NAMES.get(token, token)
    for token, actor in ACTOR_NAMES.items():
        if token in folded and any(word in folded for word in ("live", "لایو", "라이브")):
            return actor
    return "jeonghan"


def _instagram_account(record: dict[str, Any]) -> str:
    text = str(record.get("text", "") or "")
    folded = text.casefold()
    if "jeonghaniyoo_n" in folded or "جونگهان" in folded and "استوری هانی" in folded:
        return "jeonghaniyoo_n"
    patterns = (
        r"@([a-z0-9_.]{2,30})",
        r"([a-z0-9_.]{2,30})['’]s\s+instagram",
        r"([a-z0-9_.]{2,30})\s+(?:instagram|ig\s+story)",
    )
    for pattern in patterns:
        match = re.search(pattern, folded)
        if match:
            return match.group(1).strip("._")
    return str(record.get("source_username", "") or "instagram").lstrip("@")


def infer_stream(record: dict[str, Any], category: str = "") -> dict[str, str]:
    hinted_kind = str(record.get("event_hint_kind", "") or "").strip()
    hinted_key = str(record.get("event_hint_key", "") or "").strip()
    if hinted_kind and hinted_key:
        return {
            "kind": hinted_kind,
            "key": hinted_key,
            "title": str(record.get("event_hint_title", "") or "رویداد انتخابی"),
            "actor": str(record.get("event_hint_actor", "") or ""),
            "account": str(record.get("event_hint_account", "") or ""),
        }

    text = " ".join(
        (
            str(record.get("text", "") or ""),
            str(record.get("source_context", "") or ""),
        )
    )
    folded = text.casefold()
    day = parse_record_date(record).strftime("%Y-%m-%d")
    category = category.casefold()

    if any(term in folded for term in ("weverse live", " live", "لایو", "라이브")):
        actor = _find_live_actor(text)
        return {
            "kind": "live",
            "key": f"live:{day}:{actor}",
            "title": f"🎙 لایو {actor} — {day}",
            "actor": actor,
            "account": "",
        }

    if any(term in folded for term in ("instagram", "ig story", "insta", "استوری", "کامنت")) or category == "comment_or_story":
        account = _instagram_account(record)
        own = account.casefold() == "jeonghaniyoo_n"
        return {
            "kind": "instagram_jeonghan" if own else "instagram_member",
            "key": f"instagram:{day}:{account.casefold()}",
            "title": f"📷 اینستاگرام @{account} — {day}",
            "actor": "",
            "account": account,
        }

    if category == "reminder" or any(term in folded for term in ("reminder", "ریمایندر", "یادآوری")):
        kind = "reminder"
        title = f"🗓 یادآوری‌ها — {day}"
    elif category == "news" or any(term in folded for term in ("official", "announcement", "اعلام", "منتشر")):
        kind = "news"
        title = f"📰 خبرها — {day}"
    elif int(record.get("video_count", 0) or 0) > 0:
        kind = "video"
        title = f"🎞 ویدیوها — {day}"
    elif int(record.get("photo_count", 0) or 0) > 0:
        kind = "photo"
        title = f"🖼 عکس‌ها — {day}"
    else:
        kind = "general"
        title = f"🪽 آپدیت‌های دیگر — {day}"
    return {
        "kind": kind,
        "key": f"{kind}:{day}",
        "title": title,
        "actor": "",
        "account": "",
    }


def organize_record_pairs(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Group one event together and order every event from oldest to newest."""
    chronological = sorted(pairs, key=lambda pair: parse_record_date(pair[0]))
    inferred = [infer_stream(record) for record, _source in chronological]

    # A fanbase's first live tweet normally names the live, while the following
    # translation replies may contain dialogue only. X keeps them under one
    # conversationId, so propagate the live/Instagram identity to every reply.
    conversation_meta: dict[str, dict[str, str]] = {}
    for (record, _source), meta in zip(chronological, inferred):
        conversation_id = str(record.get("conversation_id", "") or "")
        if not conversation_id or meta["kind"] not in {
            "live",
            "instagram_jeonghan",
            "instagram_member",
        }:
            continue
        thread_meta = dict(meta)
        thread_meta["key"] = f"{meta['kind']}:thread:{conversation_id}"
        conversation_meta[conversation_id] = thread_meta

    groups: OrderedDict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = OrderedDict()
    group_meta: dict[str, dict[str, str]] = {}
    for (record, source), inferred_meta in zip(chronological, inferred):
        conversation_id = str(record.get("conversation_id", "") or "")
        meta = conversation_meta.get(conversation_id, inferred_meta)
        groups.setdefault(meta["key"], []).append((record, source))
        group_meta[meta["key"]] = meta

    ordered: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key, members in groups.items():
        meta = group_meta[key]
        total = len(members)
        for index, (record, source) in enumerate(members, start=1):
            enriched = dict(record)
            enriched["group_key"] = key
            enriched["group_title"] = meta["title"]
            enriched["group_kind"] = meta["kind"]
            enriched["group_actor"] = meta["actor"]
            enriched["group_account"] = meta["account"]
            enriched["group_index"] = index
            enriched["group_total"] = total
            ordered.append((enriched, source))
    return ordered


def _stable_template(templates: list[str], key: str) -> str:
    if not templates:
        return ""
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return templates[int.from_bytes(digest[:2], "big") % len(templates)]


def _theme_templates(config: dict[str, Any], kind: str) -> list[str]:
    theme_config = config.get("themes", {}) if isinstance(config, dict) else {}
    configured = theme_config.get("templates", {}) if isinstance(theme_config, dict) else {}
    values = configured.get(kind) if isinstance(configured, dict) else None
    if isinstance(values, str) and values.strip():
        return [values.strip()]
    if isinstance(values, list):
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if cleaned:
            return cleaned
    return DEFAULT_THEMES.get(kind, DEFAULT_THEMES["general"])


def _strip_generated_header(caption: str, stream: dict[str, str]) -> str:
    lines = caption.strip().splitlines()
    if len(lines) < 2:
        return caption.strip()
    first = lines[0].strip().casefold()
    header_terms = (
        "weverse live",
        "instagram",
        "ig story",
        "hannie update",
        "hannie news",
        "hannie video",
    )
    date_like = bool(re.search(r"\b\d{6}\b", first))
    if date_like or any(term in first for term in header_terms):
        return "\n".join(lines[1:]).strip()
    if stream["kind"].startswith("instagram") and len(first) < 90 and any(
        term in first for term in ("استوری", "کامنت", "پست هانی")
    ):
        return "\n".join(lines[1:]).strip()
    return caption.strip()


def ensure_rtl_caption(caption: str) -> str:
    body = caption.strip()
    persian_match = PERSIAN_RE.search(body)
    if not body or not persian_match:
        return body
    if body.startswith(RLM):
        return body
    if body.startswith("،،"):
        return RLM + body
    first = body[0]
    if PERSIAN_RE.match(first):
        return body
    # An invisible RLM establishes direction; the visible Persian-style comma
    # prefix prevents Telegram from placing Tumblr symbols at the wrong edge.
    prefix = body[: persian_match.start()]
    if LEADING_DECORATION_RE.match(body) or (
        prefix and not re.search(r"[A-Za-z0-9\u0600-\u06ff]", prefix)
    ):
        return RLM + "،، " + body
    return RLM + body


def apply_post_theme(
    caption: str,
    record: dict[str, Any],
    category: str,
    config: dict[str, Any],
) -> tuple[str, str]:
    theme_config = config.get("themes", {}) if isinstance(config, dict) else {}
    if isinstance(theme_config, dict) and not bool(theme_config.get("enabled", True)):
        return ensure_rtl_caption(caption), "disabled"

    stream = infer_stream(record, category)
    kind = stream["kind"]
    templates = _theme_templates(config, kind)
    template = _stable_template(templates, stream["key"])
    timezone_name = str(theme_config.get("timezone", "Asia/Tehran") or "Asia/Tehran")
    header = template.format(
        date=local_date_code(record, timezone_name),
        actor=stream["actor"] or "jeonghan",
        account=stream["account"] or str(record.get("source_username", "update")),
    ).strip()
    body = _strip_generated_header(caption, stream)
    themed = f"{header}\n{body}" if header and body else header or body
    return ensure_rtl_caption(themed), stream["key"]
