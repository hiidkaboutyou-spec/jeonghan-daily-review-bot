from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from app.final_edit_capture import FinalEditStore, fingerprint
from app.models import Draft, Update

SCHEMA_VERSION = 1
DEFAULT_OUTPUT = Path(".state/exports/human-preference-pairs.jsonl")


def _load_state(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("state file must contain a JSON object")
    return raw


def _draft_fingerprint(caption: str) -> str:
    return fingerprint("authoritative-review-draft-v1", str(caption or ""))


def _prompt(update: Update, content_type: str) -> str:
    source = update.translation_source().strip()
    return (
        "Produce the final Persian channel-ready text for this Jeonghan update. "
        "Preserve factual meaning and only change wording/style when it improves the final post.\n"
        f"Content type: {content_type or 'OTHER'}\n"
        f"Source:\n{source}"
    )


def build_preference_rows(
    state: dict[str, Any],
    store: FinalEditStore,
    *,
    include_undecided: bool = False,
    limit: int = 10_000,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    drafts = state.get("drafts")
    archive = state.get("archive")
    if not isinstance(drafts, dict):
        drafts = {}
    if not isinstance(archive, dict):
        archive = {}

    records = store.list_active_final_edits(
        eligible_only=not include_undecided,
        limit=limit,
    )
    rows: list[dict[str, Any]] = []
    skipped = {
        "missing_draft": 0,
        "stale_draft": 0,
        "missing_update": 0,
        "empty_pair": 0,
    }

    for record in records:
        # Explicitly ineligible edits are never preference labels, even when the
        # caller asks to include undecided records for manual research.
        if record.calibration_eligible == "ineligible":
            continue
        raw_draft = drafts.get(record.draft_id)
        if not isinstance(raw_draft, dict):
            skipped["missing_draft"] += 1
            continue
        try:
            draft = Draft.from_dict(raw_draft)
        except (TypeError, ValueError):
            skipped["missing_draft"] += 1
            continue
        if _draft_fingerprint(draft.caption) != record.authoritative_review_draft_fingerprint:
            skipped["stale_draft"] += 1
            continue

        raw_update = archive.get(record.update_id)
        if not isinstance(raw_update, dict):
            skipped["missing_update"] += 1
            continue
        try:
            update = Update.from_dict(raw_update)
        except (TypeError, ValueError, KeyError):
            skipped["missing_update"] += 1
            continue

        chosen = store.final_body(record.final_edit_id).strip()
        rejected = str(draft.caption or "").strip()
        if not chosen or not rejected or chosen == rejected:
            skipped["empty_pair"] += 1
            continue

        rows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "prompt": _prompt(update, record.content_type),
                "chosen": chosen,
                "rejected": rejected,
                "metadata": {
                    "pair_id": record.final_edit_id,
                    "draft_id": record.draft_id,
                    "update_id": record.update_id,
                    "event_id": record.event_id,
                    "segment_id": record.segment_id,
                    "content_type": record.content_type,
                    "confirmed_at": record.confirmed_at,
                    "provenance": "user_confirmed_final_edit",
                    "calibration_eligible": record.calibration_eligible,
                    "source_url": update.url,
                },
            }
        )

    summary = {
        "records_considered": len(records),
        "pairs_exported": len(rows),
        **skipped,
    }
    return rows, summary


def write_jsonl_atomic(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(path.name + ".tmp")
    with staged.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(staged, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def export_preferences(
    *,
    state_path: Path,
    private_db_path: Path,
    output_path: Path,
    include_undecided: bool = False,
    limit: int = 10_000,
) -> dict[str, int]:
    if not state_path.is_file():
        raise FileNotFoundError(f"state file not found: {state_path}")
    if not private_db_path.is_file():
        raise FileNotFoundError(f"private review database not found: {private_db_path}")
    state = _load_state(state_path)
    store = FinalEditStore(private_db_path)
    try:
        rows, summary = build_preference_rows(
            state,
            store,
            include_undecided=include_undecided,
            limit=limit,
        )
    finally:
        store.close()
    write_jsonl_atomic(rows, output_path)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export confirmed human final edits as local prompt/chosen/rejected preference pairs. "
            "The output contains private source and final-edit text; keep it local."
        )
    )
    parser.add_argument("--state", type=Path, default=Path(".state/state.json"))
    parser.add_argument("--private-db", type=Path, default=Path(".state/private-review.sqlite3"))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-undecided", action="store_true")
    parser.add_argument("--limit", type=int, default=10_000)
    args = parser.parse_args()

    summary = export_preferences(
        state_path=args.state,
        private_db_path=args.private_db,
        output_path=args.out,
        include_undecided=args.include_undecided,
        limit=args.limit,
    )
    print(json.dumps(summary, sort_keys=True))
    print(f"private preference export written locally: {args.out}")
    print("Do not commit or upload this file automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
