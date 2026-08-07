from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from .archive_store import ArchiveStore
from .models import Update

BACKUP_FORMAT = "jeonghan-private-archive-jsonl-v1"


def export_archive_jsonl(db_path: Path, output_path: Path) -> int:
    """Export ONLY private archive records, never other DB tables or credentials."""
    db_path = Path(db_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    conn = sqlite3.connect(db_path, timeout=15)
    try:
        rows = conn.execute(
            "SELECT raw_json, translated_text, caption FROM archive_records ORDER BY created_at, update_id"
        ).fetchall()
    finally:
        conn.close()

    fd, temp_name = tempfile.mkstemp(
        prefix=output_path.name + ".",
        suffix=".tmp",
        dir=str(output_path.parent),
        text=True,
    )
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"format": BACKUP_FORMAT}, ensure_ascii=False) + "\n")
            for raw_json, translated_text, caption in rows:
                try:
                    update = json.loads(raw_json)
                    Update.from_dict(update)
                except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                    continue
                record = {
                    "update": update,
                    "translated_text": str(translated_text or ""),
                    "caption": str(caption or ""),
                }
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, output_path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return count


def import_archive_jsonl(store: ArchiveStore, input_path: Path) -> tuple[int, int]:
    """Import valid archive rows without deleting or replacing existing data."""
    imported = 0
    skipped = 0
    path = Path(input_path)
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        try:
            header = json.loads(first)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid archive backup header") from exc
        if not isinstance(header, dict) or header.get("format") != BACKUP_FORMAT:
            raise ValueError("Unsupported archive backup format")

        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict) or not isinstance(record.get("update"), dict):
                    raise ValueError("bad record")
                update = Update.from_dict(record["update"])
                store.index_update(
                    update,
                    translated_text=str(record.get("translated_text") or ""),
                    caption=str(record.get("caption") or ""),
                )
                imported += 1
            except (json.JSONDecodeError, TypeError, ValueError, KeyError):
                skipped += 1
    return imported, skipped
