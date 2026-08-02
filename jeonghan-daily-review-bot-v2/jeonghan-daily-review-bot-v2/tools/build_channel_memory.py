#!/usr/bin/env python3
"""Build a privacy-safe style memory from Telegram Desktop JSON exports.

Only public message text and minimal style metadata are written. Media paths,
user IDs, reactions, phone data, and export metadata never enter the output.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


ARABIC_RE = re.compile(r"[\u0600-\u06ff]")
LATIN_RE = re.compile(r"[A-Za-z]")
URL_RE = re.compile(r"(?:https?://|t\.me/|www\.)\S+", re.IGNORECASE)

CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "instagram": ("instagram", "insta", "ig story", "인스타", "کامنت", "استوری"),
    "live": ("weverse live", "livestream", "live", "라이브", "لایو", "ویورس"),
    "photo": ("photo", "photos", "selfie", "photoshoot", "pictorial", "사진", "셀카", "عکس", "سلفی", "فتوشوت"),
    "video": ("video", "clip", "reel", "tiktok", "영상", "ویدیو", "کلیپ", "ریلز"),
    "news": ("official", "announcement", "released", "release", "schedule", "공지", "공식", "اعلام", "منتشر", "انتشار"),
    "magazine": ("magazine", "cover", "مجله", "کاور", "화보"),
    "performance": ("performance", "stage", "dance", "concert", "fancam", "무대", "댄스", "اجرا", "استیج", "رقص", "کنسرت", "فنکم"),
    "cute": ("cute", "adorable", "cutie", "soft", "pretty", "beautiful", "lovely", "귀여", "예쁘", "ناز", "بامزه", "خوشگل", "عسلی", "کیوت", "قشنگ", "دوست‌داشتنی"),
    "fashion": ("fashion", "outfit", "brand", "لباس", "استایل", "برند", "패션"),
    "airport": ("airport", "departure", "arrival", "فرودگاه", "출국", "입국", "공항"),
    "military": ("military", "enlist", "service", "سربازی", "군대", "입대"),
    "health": ("health", "injury", "hospital", "sick", "سلامت", "آسیب", "بیمار", "건강", "부상"),
    "birthday": ("birthday", "생일", "تولد"),
    "fansign": ("fansign", "fancall", "fan sign", "fan call", "팬싸", "팬콜", "فن‌ساین", "فن کال", "فن‌کال"),
    "reminder": ("reminder", "throwback", "on this day", "ریمایندر", "یادآوری"),
}


def flatten_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(str(item.get("text", "") or ""))
    return "".join(parts)


def clean_text(value: Any) -> str:
    text = flatten_text(value).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u200e", "").replace("\ufeff", "")
    text = URL_RE.sub("", text)
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize(text: str) -> str:
    value = text.casefold()
    value = re.sub(r"[@#]([\w_]+)", r"\1", value, flags=re.UNICODE)
    value = re.sub(r"[^\w\u0600-\u06ff\u3131-\u318e\uac00-\ud7a3]+", " ", value)
    return " ".join(value.split())


def language(text: str) -> str:
    persian = len(ARABIC_RE.findall(text))
    latin = len(LATIN_RE.findall(text))
    if persian >= 4 and latin >= 4:
        return "mixed"
    if persian >= 4:
        return "persian"
    if latin >= 4:
        return "english"
    return "other"


def concepts(text: str) -> list[str]:
    folded = text.casefold()
    return sorted(
        concept
        for concept, terms in CONCEPT_TERMS.items()
        if any(term in folded for term in terms)
    )


def has_media(message: dict[str, Any]) -> bool:
    return any(message.get(key) for key in ("photo", "file", "media_type", "thumbnail"))


def media_concepts(message: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    if message.get("photo"):
        found.add("photo")
    media_type = str(message.get("media_type", "") or "").casefold()
    mime_type = str(message.get("mime_type", "") or "").casefold()
    if media_type in {"video_file", "animation", "video_message"} or mime_type.startswith("video/"):
        found.add("video")
    return found


def category(text: str, message: dict[str, Any], found: list[str]) -> str:
    folded = text.casefold()
    if "instagram" in found or any(term in folded for term in ("کامنت", "استوری", "댓글")):
        return "comment_or_story"
    if "reminder" in found:
        return "reminder"
    dialogue_markers = len(re.findall(r"(?m)^[^\n]{0,16}(?::|：)", text))
    if "live" in found or dialogue_markers >= 2 or any(mark in text for mark in ("🪽:", "💎:", "🍒:", "👤:")):
        return "dialogue_translation"
    if "news" in found or "magazine" in found:
        return "news"
    if has_media(message):
        return "reaction"
    if any(mark in text for mark in ("😭", "😂", "😡", ":((", ":(((")):
        return "fandom_humor"
    return "general"


def load_export(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as first_error:
        # Telegram sometimes splits a large export before writing the final ]}.
        # Repair only that narrow case and still fail on genuinely corrupt JSON.
        try:
            value = json.loads(raw.rstrip() + "\n ]\n}")
        except json.JSONDecodeError:
            raise ValueError(f"Invalid Telegram JSON: {path}: {first_error}") from first_error
    if not isinstance(value, dict) or not isinstance(value.get("messages"), list):
        raise ValueError(f"Telegram export has no messages list: {path}")
    return value


def safe_message(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("type") != "message":
        return None
    text = clean_text(message.get("text", ""))
    normalized = normalize(text)
    lang = language(text)
    if len(normalized) < 6 or lang not in {"persian", "mixed"}:
        return None
    date = str(message.get("date", "") or "")
    try:
        year = int(date[:4])
    except (TypeError, ValueError):
        year = 0
    found = sorted(set(concepts(text)) | media_concepts(message))
    return {
        "id": str(message.get("id", "") or ""),
        "date": date,
        "year": year,
        "category": category(text, message, found),
        "language": lang,
        "concepts": found,
        "kind": "message",
        "has_media": has_media(message),
        "reply_to": str(message.get("reply_to_message_id", "") or ""),
        "text": text[:2400],
    }


def build_entries(paths: list[Path]) -> list[dict[str, Any]]:
    messages_by_id: dict[str, dict[str, Any]] = {}
    for path in paths:
        export = load_export(path)
        for raw in export["messages"]:
            if isinstance(raw, dict) and raw.get("id") is not None:
                messages_by_id[str(raw["id"])] = raw

    entries: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for message in sorted(messages_by_id.values(), key=lambda item: int(item.get("id", 0) or 0)):
        entry = safe_message(message)
        if not entry:
            continue
        normalized = normalize(entry["text"])
        if normalized in seen:
            continue
        seen.add(normalized)
        entries.append(entry)
        by_id[entry["id"]] = entry

    # Preserve real two-message Telegram cadence for replies without exposing
    # any private metadata. These are style sequences, not factual templates.
    sequences: list[dict[str, Any]] = []
    for child in entries:
        parent = by_id.get(child["reply_to"])
        if not parent or parent["year"] != child["year"]:
            continue
        text = f"{parent['text']}\n\n{child['text']}"
        if len(text) > 1800 or normalize(text) in seen:
            continue
        seen.add(normalize(text))
        found = sorted(set(parent["concepts"]) | set(child["concepts"]))
        sequences.append(
            {
                "id": f"{parent['id']}+{child['id']}",
                "date": child["date"],
                "year": child["year"],
                "category": child["category"] if child["category"] != "general" else parent["category"],
                "language": language(text),
                "concepts": found,
                "kind": "reply_sequence",
                "has_media": bool(parent["has_media"] or child["has_media"]),
                "reply_to": "",
                "text": text,
            }
        )
    return entries + sequences


def build_profile(entries: list[dict[str, Any]]) -> dict[str, Any]:
    lengths = [len(entry["text"]) for entry in entries if entry["kind"] == "message"]
    line_counts = [entry["text"].count("\n") + 1 for entry in entries if entry["kind"] == "message"]
    years = Counter(str(entry["year"]) for entry in entries if entry["kind"] == "message")
    categories = Counter(entry["category"] for entry in entries if entry["kind"] == "message")
    languages = Counter(entry["language"] for entry in entries if entry["kind"] == "message")
    return {
        "schema_version": 1,
        "source": "sanitized public Telegram channel text",
        "message_examples": sum(entry["kind"] == "message" for entry in entries),
        "reply_sequences": sum(entry["kind"] == "reply_sequence" for entry in entries),
        "years": dict(sorted(years.items())),
        "categories": dict(categories.most_common()),
        "languages": dict(languages.most_common()),
        "median_chars": round(statistics.median(lengths)) if lengths else 0,
        "median_lines": round(statistics.median(line_counts), 1) if line_counts else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exports", nargs="+", type=Path, help="Telegram result*.json files")
    parser.add_argument("--output", type=Path, required=True, help="channel_memory.jsonl path")
    parser.add_argument("--profile-output", type=Path, help="optional voice profile JSON path")
    args = parser.parse_args()

    entries = build_entries(args.exports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    profile = build_profile(entries)
    if args.profile_output:
        args.profile_output.parent.mkdir(parents=True, exist_ok=True)
        args.profile_output.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
