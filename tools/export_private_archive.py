from __future__ import annotations

import argparse
from pathlib import Path

from app.archive_backup import export_archive_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the private review archive only.")
    parser.add_argument("--db", default=".state/private-review.sqlite3")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    count = export_archive_jsonl(Path(args.db), Path(args.output))
    print(f"Exported {count} private archive record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
