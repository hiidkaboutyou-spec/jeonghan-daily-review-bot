from __future__ import annotations

import argparse
import hashlib
import json
import re
from json import JSONDecoder
from pathlib import Path

PERSIAN = re.compile(r"[\u0600-\u06ff]")
KOREAN = re.compile(r"[\uac00-\ud7af]")
JAPANESE = re.compile(r"[\u3040-\u30ff]")
LATIN = re.compile(r"[A-Za-z]")
SPEAKER = re.compile(r"^\s*([^\s:：]{1,20})\s*[:：]\s*(.+)$", re.M)
LAUGHTER = re.compile(r"(?:ㅋ{2,}|ㅎ{2,}|(?:lol|lmao|lmfao)\b|خ{2,}|ه{3,})", re.I)


def visible_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(x if isinstance(x, str) else str(x.get("text", "")) if isinstance(x, dict) else "" for x in value)
    return ""


def load_export(path: Path):
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
        return data, list(data.get("messages", [])), False
    except json.JSONDecodeError:
        marker = raw.find('"messages"')
        prefix = raw[:marker]
        channel_match = re.search(r'"id"\s*:\s*(-?\d+)', prefix)
        name_match = re.search(r'"name"\s*:\s*"([^"]*)"', prefix)
        head = {"id": int(channel_match.group(1)) if channel_match else "", "name": name_match.group(1) if name_match else ""}
        start = raw.find("[", marker) + 1
        dec = JSONDecoder()
        messages = []
        while start > 0 and start < len(raw):
            while start < len(raw) and raw[start] in " \r\n\t,":
                start += 1
            if start >= len(raw) or raw[start] == "]":
                break
            try:
                obj, end = dec.raw_decode(raw, start)
            except json.JSONDecodeError:
                break
            if isinstance(obj, dict):
                messages.append(obj)
            start = end
        return head, messages, True


def language(text: str) -> str:
    ko, ja, fa, en = bool(KOREAN.search(text)), bool(JAPANESE.search(text)), bool(PERSIAN.search(text)), bool(LATIN.search(text))
    if ko and ja:
        return "mixed"
    if ko:
        return "ko"
    if ja:
        return "ja"
    if fa and en:
        return "fa_mixed"
    if fa:
        return "fa"
    if en:
        return "en"
    return "other"


def content_type(text: str) -> str:
    l = text.casefold()
    lines = [x for x in text.splitlines() if x.strip()]
    if ("weverse" in l or "ویورس" in l or "위버스" in l) and "live" in l:
        return "WEVERSE_LIVE"
    if "weverse" in l or "ویورس" in l or "위버스" in l:
        return "WEVERSE_POST"
    checks = [
        ("FANFIC_UPDATE", ("fanfic", "ao3", "فیک")),
        ("FANSIGN", ("fansign", "fan sign", "فن ساین", "فن‌ساین", "팬싸", "fancall", "fan call", "فن‌کال")),
        ("INTERVIEW", ("interview", "مصاحبه", "インタビュー", "인터뷰")),
        ("MAGAZINE", ("magazine", "مجله", "vogue", "allure", "elle", "gq ")),
        ("AIRPORT", ("airport", "فرودگاه", "공항", "空港")),
        ("INSTAGRAM_UPDATE", ("instagram", "اینستاگرام", "insta")),
        ("BRAND_AD", ("brand", "کمپین", "campaign", "ambassador", "سفیر", "banila", "بانیلا")),
        ("FASHION_EVENT", ("fashion week", "فشن", "fashion event", "showroom")),
        ("OFFICIAL_NEWS", ("official", "공지", "notice", "اعلام", "اطلاعیه", "pledis", "hybe")),
        ("WORDPLAY", ("wordplay", "pun", "بازی با کلمه", "말장난", "言葉遊び")),
    ]
    for name, keys in checks:
        if any(k in l for k in keys):
            return name
    if len(SPEAKER.findall(text)) >= 2:
        return "LIVE_DIALOGUE"
    if len(text) > 500 or len(lines) >= 7:
        return "THREAD_OR_LONG_EXPLANATION"
    if any(k in l for k in ("op ", "op:", "اوپ", "fan account", "fanaccount", "فنی که", "تعریف کرد")):
        return "FAN_ACCOUNT_OR_OP_STORY"
    if any(q in text for q in ("“", "”", "«", "»")) and len(text) < 400:
        return "MEMBER_QUOTE"
    if any(k in l for k in ("interaction", "جونگچول", "جیهان", "couphan", "gyuhan", "باهم", "همدیگه")):
        return "MEMBER_INTERACTION"
    reaction = bool(re.search(r"(?:😭|🥺|💗|🩷|💘|گریه|کیوت|ناز|عسلی|تاینی|دارم میمیرم|می‌میرم)", text))
    if len(text) <= 100 and reaction:
        return "SHORT_REACTION"
    if re.search(r"\b(?:20\d{2}|\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\b", text) and not reaction:
        return "FACTUAL_INFORMATION"
    if LATIN.search(text) and not PERSIAN.search(text):
        return "X_FANBASE_UPDATE"
    return "OTHER"


def source_label(path: Path) -> str:
    name = path.stem.casefold()
    if "result 3" in name:
        return "result 3"
    if "result 4" in name:
        return "result 4"
    raise ValueError(f"Cannot determine source export label from {path.name!r}")


def make_row(channel: str, message: dict, source_export: str) -> dict:
    text = visible_text(message.get("text", "")).strip()
    ct = content_type(text)
    return {
        "version": 1,
        "example_id": f"{channel}:{message.get('id')}",
        "channel_id": channel,
        "message_id": str(message.get("id", "")),
        "text": text,
        "date": str(message.get("date", "")),
        "source_export": source_export,
        "source_language": language(text),
        "content_type": ct,
        "line_count": max(1, text.count("\n") + 1),
        "char_count": len(text),
        "has_dialogue": len(SPEAKER.findall(text)) >= 2 or ct == "LIVE_DIALOGUE",
        "has_laughter": bool(LAUGHTER.search(text)),
        "has_media": bool(message.get("photo") or message.get("file") or message.get("media_type")),
        "format_prefix": text.splitlines()[0][:40],
        "base_style_weight": 1.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("exports", nargs="+", type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path("data/channel_style"))
    ap.add_argument("--shard-size", type=int, default=1631)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    unique: dict[tuple[str, str], dict] = {}
    export_stats: dict[str, dict] = {}
    for path in args.exports:
        label = source_label(path)
        meta, messages, truncated = load_export(path)
        channel = str(meta.get("id", ""))
        textual = 0
        for message in messages:
            text = visible_text(message.get("text", "")).strip()
            if message.get("type") != "message" or not text:
                continue
            textual += 1
            key = (channel, str(message.get("id", "")))
            if key not in unique:
                unique[key] = make_row(channel, message, label)
        export_stats[label] = {
            "complete_messages_recovered": len(messages),
            "valid_textual_messages": textual,
            "incomplete_tail_ignored": bool(truncated),
        }

    def sort_key(row: dict):
        mid = str(row["message_id"])
        return (str(row["channel_id"]), int(mid) if mid.lstrip("-").isdigit() else mid)

    rows = sorted(unique.values(), key=sort_key)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("examples-*.jsonl"):
        old.unlink()
    shards = []
    size = max(1, int(args.shard_size))
    for index, start in enumerate(range(0, len(rows), size), 1):
        chunk = rows[start:start + size]
        filename = f"examples-{index:04d}.jsonl"
        path = out_dir / filename
        text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in chunk)
        raw = text.encode("utf-8")
        path.write_bytes(raw)
        shards.append({"filename": filename, "example_count": len(chunk), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})

    result3 = sum(1 for row in rows if row["source_export"] == "result 3")
    result4 = sum(1 for row in rows if row["source_export"] == "result 4")
    manifest = {
        "authority_message_count": len(rows),
        "channel_style_version": 1,
        "chronological_base_weight": 1.0,
        "corpus_format_version": 1,
        "date_score_contribution": 0.0,
        "deduplication": "stable channel_id + message_id only",
        "recency_weighting": "NONE",
        "result_3_contribution": result3,
        "result_4_contribution": result4,
        "shards": shards,
        "text_similarity_deduplication": False,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    if args.report:
        report = {
            "version": 1,
            "unique_textual_messages": len(rows),
            "source_stats": export_stats,
            "stable_identity_deduplication": True,
            "text_similarity_deduplication": False,
            "chronological_weighting": "none",
            "base_style_weight": 1.0,
        }
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
