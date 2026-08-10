from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path

from .channel_style_runtime import CHANNEL_STYLE_VERSION, CORPUS_FORMAT_VERSION, ChannelStyleMemory
from .config import ConfigError, ROOT, Settings

EXPECTED_AUTHORITY = 16306
EXPECTED_RESULT_3 = 15206
EXPECTED_RESULT_4 = 1100


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


def _validate_manifest_corpus(root: Path, errors: list[str]) -> int:
    corpus_dir = root / "data" / "channel_style"
    manifest_path = corpus_dir / "manifest.json"
    manifest = _load_object(manifest_path, "channel style corpus manifest", errors)
    if not manifest:
        return 0

    if int(manifest.get("channel_style_version", 0) or 0) != CHANNEL_STYLE_VERSION:
        errors.append("Corpus manifest has the wrong channel_style_version.")
    if int(manifest.get("corpus_format_version", 0) or 0) != CORPUS_FORMAT_VERSION:
        errors.append("Corpus manifest has the wrong corpus_format_version.")
    if int(manifest.get("authority_message_count", 0) or 0) != EXPECTED_AUTHORITY:
        errors.append("Corpus manifest authority_message_count must equal 16,306.")
    if float(manifest.get("chronological_base_weight", 0) or 0) != 1.0:
        errors.append("Corpus manifest chronological_base_weight must be 1.0.")
    if str(manifest.get("recency_weighting", "")).upper() != "NONE":
        errors.append("Corpus manifest recency_weighting must be NONE.")
    if float(manifest.get("date_score_contribution", -1) or 0) != 0.0:
        errors.append("Corpus manifest date_score_contribution must be 0.0.")
    if str(manifest.get("deduplication", "")) != "stable channel_id + message_id only":
        errors.append("Corpus manifest must certify stable channel_id + message_id deduplication only.")
    if manifest.get("text_similarity_deduplication") is not False:
        errors.append("Corpus manifest must certify no text-similarity deduplication.")
    if int(manifest.get("result_3_contribution", 0) or 0) != EXPECTED_RESULT_3:
        errors.append("Corpus manifest result 3 contribution must equal 15,206.")
    if int(manifest.get("result_4_contribution", 0) or 0) != EXPECTED_RESULT_4:
        errors.append("Corpus manifest result 4 contribution must equal 1,100.")

    shards = manifest.get("shards", [])
    if not isinstance(shards, list) or not shards:
        errors.append("Corpus manifest contains no shards.")
        return 0

    seen: set[str] = set()
    source_counts: Counter[str] = Counter()
    weights: set[float] = set()
    manifest_count = 0
    previous_name = ""
    for entry in shards:
        if not isinstance(entry, dict):
            errors.append("Corpus manifest contains an invalid shard entry.")
            continue
        filename = str(entry.get("filename", "")).strip()
        if not filename or Path(filename).name != filename or not filename.endswith(".jsonl"):
            errors.append("Corpus manifest contains an unsafe/invalid shard filename.")
            continue
        if previous_name and filename <= previous_name:
            errors.append("Corpus manifest shard filenames are not in deterministic ascending order.")
        previous_name = filename
        path = corpus_dir / filename
        if not path.exists() or path.stat().st_size <= 0:
            errors.append(f"Corpus shard missing/empty: data/channel_style/{filename}")
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(entry.get("sha256", "")):
            errors.append(f"Corpus shard SHA-256 mismatch: {filename}")
        if int(entry.get("bytes", -1)) != len(raw):
            errors.append(f"Corpus shard byte count mismatch: {filename}")
        local_count = 0
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if not isinstance(item, dict):
                        raise ValueError("row is not an object")
                    eid = str(item.get("example_id", "")).strip()
                    if not eid or eid in seen:
                        errors.append("Derived channel corpus contains a missing/duplicate stable example_id.")
                        continue
                    seen.add(eid)
                    source_counts[str(item.get("source_export", ""))] += 1
                    weights.add(float(item.get("base_style_weight", 0) or 0))
                    local_count += 1
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            errors.append(f"Corpus shard is corrupt/unreadable: {filename}")
            continue
        if local_count != int(entry.get("example_count", -1)):
            errors.append(f"Corpus shard example count mismatch: {filename}")
        manifest_count += local_count

    if manifest_count != EXPECTED_AUTHORITY or len(seen) != EXPECTED_AUTHORITY:
        errors.append(f"Derived channel corpus contains {len(seen)} unique examples; expected 16306.")
    if source_counts.get("result 3", 0) != EXPECTED_RESULT_3:
        errors.append(f"Derived channel corpus result 3 count is {source_counts.get('result 3', 0)}; expected 15206.")
    if source_counts.get("result 4", 0) != EXPECTED_RESULT_4:
        errors.append(f"Derived channel corpus result 4 count is {source_counts.get('result 4', 0)}; expected 1100.")
    if weights != {1.0}:
        errors.append("Every historical channel example must have base_style_weight=1.0.")
    return len(seen)


def validate_channel_style_system(root: Path = ROOT) -> tuple[list[str], int]:
    root = Path(root)
    errors: list[str] = []
    profile = _load_object(root / "config" / "channel_style_profile.json", "channel style profile", errors)
    glossary = _load_object(root / "config" / "channel_glossary.json", "channel glossary", errors)
    report = _load_object(root / "data" / "channel_style_build_report.json", "channel style build report", errors)

    authority = profile.get("authority", {}) if isinstance(profile, dict) else {}
    if int(profile.get("channel_style_version", profile.get("version", 0)) or 0) != CHANNEL_STYLE_VERSION:
        errors.append("channel_style_profile.json has the wrong channel style version.")
    if int(profile.get("corpus_format_version", 0) or 0) != CORPUS_FORMAT_VERSION:
        errors.append("channel_style_profile.json has the wrong corpus_format_version.")
    if int(authority.get("unique_textual_messages", profile.get("unique_textual_messages", 0)) or 0) != EXPECTED_AUTHORITY:
        errors.append("Channel style profile authority must contain exactly 16,306 unique textual messages.")
    if float(authority.get("chronological_base_weight", profile.get("base_style_weight", 0)) or 0) != 1.0:
        errors.append("Channel style historical base weight must be 1.0.")
    if str(authority.get("recency_weighting", profile.get("chronological_weighting", ""))).upper() != "NONE":
        errors.append("Channel style recency weighting must be NONE.")
    if float(authority.get("date_score_contribution", 0.0) or 0.0) != 0.0:
        errors.append("Historical date must contribute zero to style score.")

    if int(glossary.get("channel_style_version", 0) or 0) != CHANNEL_STYLE_VERSION:
        errors.append("channel_glossary.json has the wrong channel_style_version.")
    if not isinstance(glossary.get("categories"), dict) or not glossary.get("categories"):
        errors.append("channel_glossary.json has no usable categories.")
    if int(glossary.get("authority_message_count", 0) or 0) != EXPECTED_AUTHORITY:
        errors.append("channel_glossary.json authority count does not match 16,306.")

    if int(report.get("unique_textual_messages", 0) or 0) != EXPECTED_AUTHORITY:
        errors.append("channel_style_build_report.json does not certify 16,306 messages.")
    if report.get("text_similarity_deduplication") is not False:
        errors.append("Style corpus must not use text-similarity deduplication.")
    if str(report.get("chronological_weighting", "")).lower() != "none":
        errors.append("Style corpus build report must certify no chronological weighting.")
    if float(report.get("base_style_weight", 0) or 0) != 1.0:
        errors.append("Style corpus build report must certify base_style_weight=1.0.")
    source_stats = report.get("source_stats", {}) if isinstance(report, dict) else {}
    if int(source_stats.get("result 3", {}).get("valid_textual_messages", 0) or 0) != EXPECTED_RESULT_3:
        errors.append("Build report result 3 authority count must equal 15,206.")
    if int(source_stats.get("result 4", {}).get("valid_textual_messages", 0) or 0) != EXPECTED_RESULT_4:
        errors.append("Build report result 4 authority count must equal 1,100.")

    count = _validate_manifest_corpus(root, errors)
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
