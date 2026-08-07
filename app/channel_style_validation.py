from __future__ import annotations

import gzip
import json
import tempfile
from pathlib import Path

from .channel_style_runtime import CHANNEL_STYLE_VERSION, CORPUS_FORMAT_VERSION, ChannelStyleMemory
from .config import ConfigError, ROOT, Settings

EXPECTED_AUTHORITY = 16306


def _load_object(path: Path, label: str, errors: list[str]) -> dict:
    if not path.exists() or path.stat().st_size <= 0:
        errors.append(f"Required channel-style artifact is missing/empty: {path.relative_to(ROOT)}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append(f"Invalid {label}: {path.relative_to(ROOT)}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object: {path.relative_to(ROOT)}")
        return {}
    return value


def validate_channel_style_system(root: Path = ROOT) -> tuple[list[str], int]:
    root = Path(root)
    errors: list[str] = []
    corpus = root / "data" / "channel_style_examples.jsonl.gz"
    profile_path = root / "config" / "channel_style_profile.json"
    glossary_path = root / "config" / "channel_glossary.json"
    report_path = root / "data" / "channel_style_build_report.json"

    profile = _load_object(profile_path, "channel style profile", errors)
    glossary = _load_object(glossary_path, "channel glossary", errors)
    report = _load_object(report_path, "channel style build report", errors)

    authority = profile.get("authority", {}) if isinstance(profile, dict) else {}
    profile_version = profile.get("channel_style_version", profile.get("version", 0)) if isinstance(profile, dict) else 0
    authority_count = authority.get("unique_textual_messages", profile.get("unique_textual_messages", 0)) if isinstance(profile, dict) else 0
    base_weight = authority.get("chronological_base_weight", profile.get("base_style_weight", 0)) if isinstance(profile, dict) else 0
    recency = authority.get("recency_weighting", profile.get("chronological_weighting", "")) if isinstance(profile, dict) else ""
    date_score = authority.get("date_score_contribution", 0.0) if isinstance(authority, dict) else 0.0
    if int(profile_version or 0) != CHANNEL_STYLE_VERSION:
        errors.append("channel_style_profile.json has the wrong channel style version.")
    if int(profile.get("corpus_format_version", 0) or 0) != CORPUS_FORMAT_VERSION:
        errors.append("channel_style_profile.json has the wrong corpus_format_version.")
    if int(authority_count or 0) != EXPECTED_AUTHORITY:
        errors.append("Channel style authority must contain exactly 16,306 unique textual messages.")
    if float(base_weight or 0) != 1.0:
        errors.append("Channel style historical base weight must be 1.0.")
    if str(recency).upper() not in {"NONE", "NO", "0"}:
        errors.append("Channel style recency weighting must be NONE.")
    if float(date_score or 0.0) != 0.0:
        errors.append("Historical date must contribute zero to style score.")
    if not isinstance(glossary.get("categories"), dict) or not glossary.get("categories"):
        errors.append("channel_glossary.json has no usable categories.")
    glossary_count = glossary.get("authority_message_count", glossary.get("generated_from_unique_textual_messages", 0))
    if int(glossary_count or 0) != EXPECTED_AUTHORITY:
        errors.append("channel_glossary.json authority count does not match 16,306.")
    if int(report.get("unique_textual_messages", 0) or 0) != EXPECTED_AUTHORITY:
        errors.append("channel_style_build_report.json does not certify 16,306 messages.")
    if report.get("text_similarity_deduplication") is not False:
        errors.append("Style corpus must not use text-similarity deduplication.")
    if str(report.get("chronological_weighting", "")).lower() != "none":
        errors.append("Style corpus build report must certify no chronological weighting.")

    count = 0
    if not corpus.exists() or corpus.stat().st_size <= 0:
        errors.append("Required channel-style corpus is missing/empty: data/channel_style_examples.jsonl.gz")
    else:
        try:
            seen: set[str] = set()
            sources: set[str] = set()
            weights: set[float] = set()
            with gzip.open(corpus, "rt", encoding="utf-8") as fh:
                for line in fh:
                    item = json.loads(line)
                    eid = str(item.get("example_id", ""))
                    if not eid or eid in seen:
                        errors.append("Derived channel corpus contains a missing/duplicate stable example_id.")
                        break
                    seen.add(eid)
                    sources.add(str(item.get("source_export", "")))
                    weights.add(float(item.get("base_style_weight", 0) or 0))
            count = len(seen)
            if count != EXPECTED_AUTHORITY:
                errors.append(f"Derived channel corpus contains {count} examples; expected 16306.")
            if not {"result 3", "result 4"}.issubset(sources):
                errors.append("Both result 3 and recoverable result 4 must contribute to the derived corpus.")
            if weights != {1.0}:
                errors.append("Every historical channel example must have base_style_weight=1.0.")
        except (OSError, EOFError, json.JSONDecodeError, ValueError):
            errors.append("Derived channel-style corpus is corrupt/unreadable.")

    if not errors:
        try:
            with tempfile.TemporaryDirectory(prefix="channel-style-check-") as td:
                memory = ChannelStyleMemory(root, db_path=Path(td) / "style.sqlite3")
                if memory.sample_count != EXPECTED_AUTHORITY:
                    errors.append("Channel style FTS index did not initialize all 16,306 examples.")
                else:
                    memory.conn.execute("DROP TABLE channel_style_fts")
                    memory.conn.commit()
                    rebuilt = memory.rebuild_from_derived_corpus()
                    if rebuilt != EXPECTED_AUTHORITY:
                        errors.append("Channel style FTS rebuild failed.")
                memory.close()
        except Exception as exc:
            errors.append(f"Channel style FTS initialization/rebuild failed: {type(exc).__name__}")
    return errors, count


def check_project() -> int:
    try:
        settings = Settings.load(require_secrets=False)
    except ConfigError as exc:
        print(f"CHECK FAILED: {exc}")
        return 1
    errors = settings.validate_files()
    style_errors, count = validate_channel_style_system(ROOT)
    errors.extend(style_errors)
    if errors:
        for error in errors:
            print("CHECK FAILED:", error)
        return 1
    print(f"CHECK OK: {len(settings.sources)} sources, {count} channel-style examples, recency weighting NONE")
    return 0
