from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.event_fusion import build_fingerprint, match_fingerprints  # noqa: E402
from app.models import MediaItem, Update  # noqa: E402

SAME = {"confident_same_event", "probable_same_event"}


def _update(raw: dict) -> Update:
    return Update(
        id=str(raw["id"]),
        url=f"https://x.com/{raw['author']}/status/{raw['id']}",
        author=str(raw["author"]),
        author_name=str(raw["author"]),
        text=str(raw.get("text", "")),
        created_at=datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00")),
        conversation_id=str(raw.get("conversation_id", "")),
        reply_to_id=str(raw.get("reply_to_id", "")),
        quoted_id=str(raw.get("quoted_id", "")),
        category=str(raw.get("category", "general")),
        media=[MediaItem(**item) for item in raw.get("media", [])],
    )


def run() -> tuple[int, int, list[tuple[str, str, str]]]:
    data = json.loads((ROOT / "data" / "event_fusion_benchmark.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
    allowed = {
        str(item["handle"]).lstrip("@").casefold()
        for item in sources
        if item.get("enabled", True)
    }
    failures: list[tuple[str, str, str]] = []
    for case in data["cases"]:
        left, right = _update(case["left"]), _update(case["right"])
        if left.author.casefold() not in allowed or right.author.casefold() not in allowed:
            actual = "blocked"
        else:
            decision = match_fingerprints(build_fingerprint(left), build_fingerprint(right)).decision
            actual = "same_event" if decision in SAME else "separate"
        if actual != case["expected"]:
            failures.append((case["id"], case["expected"], actual))
    return len(data["cases"]) - len(failures), len(data["cases"]), failures


def main() -> int:
    passed, total, failures = run()
    if failures:
        print(f"EVENT FUSION BENCHMARK FAILED: {passed}/{total}")
        for case_id, expected, actual in failures:
            print(f"- {case_id}: expected={expected} actual={actual}")
        return 1
    print(f"EVENT FUSION BENCHMARK OK: {passed}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
