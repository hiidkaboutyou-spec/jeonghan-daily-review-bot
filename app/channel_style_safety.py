from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXPECTED_AUTHORITY = 16306


def validate_production_style_memory(memory: Any, root: Path) -> tuple[bool, str, int]:
    """Validate committed PART 1 artifacts and recover FTS before production activation.

    This is intentionally read-only with respect to the corpus. It may rebuild only
    the derived SQLite/FTS index from the committed manifest + JSONL shards.
    """
    root = Path(root)
    manifest_path = root / "data" / "channel_style" / "manifest.json"
    profile_path = root / "config" / "channel_style_profile.json"
    glossary_path = root / "config" / "channel_glossary.json"

    try:
        manifest = _read_object(manifest_path)
        profile = _read_object(profile_path)
        glossary = _read_object(glossary_path)
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"style_artifact_{type(exc).__name__}", 0

    if int(manifest.get("authority_message_count", 0) or 0) != EXPECTED_AUTHORITY:
        return False, "manifest_authority_mismatch", 0
    if float(manifest.get("chronological_base_weight", 0) or 0) != 1.0:
        return False, "manifest_base_weight_invalid", 0
    if str(manifest.get("recency_weighting", "")).upper() != "NONE":
        return False, "manifest_recency_invalid", 0
    if float(manifest.get("date_score_contribution", 1) or 0) != 0.0:
        return False, "manifest_date_ranking_invalid", 0

    authority = profile.get("authority", {}) if isinstance(profile, dict) else {}
    authority_count = authority.get("unique_textual_messages", profile.get("unique_textual_messages", 0))
    if int(authority_count or 0) != EXPECTED_AUTHORITY:
        return False, "profile_authority_mismatch", 0
    if int(glossary.get("authority_message_count", 0) or 0) != EXPECTED_AUTHORITY:
        return False, "glossary_authority_mismatch", 0
    if not isinstance(glossary.get("categories"), dict):
        return False, "glossary_categories_invalid", 0

    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        return False, "manifest_shards_missing", 0

    declared_total = 0
    corpus_dir = manifest_path.parent
    for entry in shards:
        if not isinstance(entry, dict):
            return False, "manifest_shard_entry_invalid", 0
        filename = str(entry.get("filename", "")).strip()
        expected_hash = str(entry.get("sha256", "")).strip().lower()
        if not filename or not expected_hash:
            return False, "manifest_shard_metadata_invalid", 0
        path = corpus_dir / filename
        try:
            raw = path.read_bytes()
        except OSError:
            return False, "style_shard_missing", 0
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            return False, "style_shard_hash_mismatch", 0
        declared_total += int(entry.get("example_count", 0) or 0)
    if declared_total != EXPECTED_AUTHORITY:
        return False, "manifest_shard_count_mismatch", 0

    try:
        count = _fts_count(memory)
        if count != EXPECTED_AUTHORITY:
            rebuilt = int(memory.rebuild_from_derived_corpus() or 0)
            if rebuilt != EXPECTED_AUTHORITY:
                return False, "fts_rebuild_count_mismatch", rebuilt
            count = _fts_count(memory)
        if count != EXPECTED_AUTHORITY:
            return False, "fts_count_mismatch", count
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        logger.error("Channel style FTS unavailable after recovery: %s", type(exc).__name__)
        return False, f"fts_{type(exc).__name__}", 0
    except Exception as exc:
        logger.error("Unexpected channel style initialization failure: %s", type(exc).__name__)
        return False, f"fts_{type(exc).__name__}", 0

    return True, "", count


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise json.JSONDecodeError("expected JSON object", "", 0)
    return value


def _fts_count(memory: Any) -> int:
    conn = getattr(memory, "conn", None)
    if conn is None:
        raise RuntimeError("style memory has no SQLite connection")
    examples = int(conn.execute("SELECT count(*) FROM channel_style_examples").fetchone()[0])
    fts = int(conn.execute("SELECT count(*) FROM channel_style_fts").fetchone()[0])
    if examples != fts:
        return -1
    return examples
