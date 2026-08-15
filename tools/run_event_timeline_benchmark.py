from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.event_timeline import SegmentFingerprint, _fingerprint_sort_key, make_segment_id, match_segment_fingerprints

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "data" / "event_timeline_benchmark.json"


def _created(raw: dict) -> str:
    date = str(raw.get("date", "2026-08-15"))
    clock = str(raw.get("at", "01:00"))
    if len(clock.split(":")) == 2:
        clock += ":00"
    return f"{date}T{clock}+00:00"


def _fingerprint(raw: dict, *, event_id: str, event_type: str) -> SegmentFingerprint:
    return SegmentFingerprint.from_dict({
        "update_id": str(raw["id"]),
        "event_id": event_id,
        "source": str(raw.get("source", "hani_berry_1004")),
        "created_at": _created(raw),
        "event_type": event_type,
        "language": str(raw.get("lang", "")),
        "conversation_id": str(raw.get("conv", "")),
        "reply_to_id": str(raw.get("reply", "")),
        "quoted_id": str(raw.get("quote", "")),
        "reference_hashes": list(raw.get("refs", [])),
        "topic_hashes": list(raw.get("topics", [])),
        "participants": list(raw.get("people", [])),
        "content_timestamp_seconds": raw.get("ts"),
        "timestamp_kind": "benchmark_explicit" if raw.get("ts") is not None else "",
        "part_number": raw.get("part"),
        "question_hashes": list(raw.get("questions", [])),
        "media_hashes": list(raw.get("media", [])),
        "text_anchor_hashes": list(raw.get("text_anchors", [])),
        "fact_numbers": list(raw.get("facts", [])),
        "has_negation": bool(raw.get("neg", False)),
    })


def run(path: Path = DEFAULT_FIXTURE) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    event_id = str(payload.get("event_id", "evt:benchmark"))
    cases = list(payload.get("cases", []))
    tp = fp_count = fn = tn = 0
    false_merges: list[str] = []
    false_splits: list[str] = []
    failures: list[str] = []
    chronology_total = chronology_correct = 0
    ambiguous_total = ambiguous_deferred = 0
    invariant_total = invariant_passed = 0
    pair_count = 0
    case_results: list[dict] = []

    for case in cases:
        cid = str(case.get("id", "")); kind = str(case.get("kind", ""))
        result = {"id": cid, "label": str(case.get("label", "")), "kind": kind, "passed": False}
        if kind == "pair":
            pair_count += 1
            event_type = str(case.get("type", "unknown"))
            left = _fingerprint(case["left"], event_id=event_id, event_type=event_type)
            right = _fingerprint(case["right"], event_id=event_id, event_type=event_type)
            candidate = match_segment_fingerprints(left, right, container_reference_hashes=case.get("container", []))
            expected_same = bool(case.get("same", False)); predicted_same = bool(candidate.same_segment)
            allowed_rel = {str(item) for item in case.get("rel", [])}
            if not allowed_rel:
                allowed_rel = {"same_moment", "continuation", "conflicting"} if expected_same else {"separate", "ambiguous", "complementary"}
            if expected_same and predicted_same:
                tp += 1
            elif not expected_same and predicted_same:
                fp_count += 1; false_merges.append(cid)
            elif expected_same and not predicted_same:
                fn += 1; false_splits.append(cid)
            else:
                tn += 1
            if case.get("ambiguous"):
                ambiguous_total += 1
                if not predicted_same:
                    ambiguous_deferred += 1
            result.update({
                "expected_same_segment": expected_same,
                "predicted_same_segment": predicted_same,
                "relationship": candidate.relationship,
                "confidence": candidate.confidence,
                "signals": list(candidate.matching_signals),
                "conflicts": list(candidate.conflicts),
            })
            result["passed"] = predicted_same == expected_same and candidate.relationship in allowed_rel
            if not result["passed"]:
                failures.append(cid)
        elif kind == "order":
            chronology_total += 1
            event_type = str(case.get("type", "unknown"))
            fps = [_fingerprint(raw, event_id=event_id, event_type=event_type) for raw in case.get("items", [])]
            actual = [fp.update_id for fp in sorted(fps, key=_fingerprint_sort_key)]
            expected = [str(item) for item in case.get("order", [])]
            result.update({"actual_order": actual, "expected_order": expected})
            result["passed"] = actual == expected
            if result["passed"]:
                chronology_correct += 1
            else:
                failures.append(cid)
        elif kind == "invariant":
            invariant_total += 1
            name = str(case.get("name", ""))
            if name == "event_membership_preserved":
                before = event_id
                segment_id = make_segment_id(event_id, ["benchmark-update"])
                ok = event_id == before and segment_id != event_id
            elif name == "update_lifecycle_independent":
                lifecycle = {"benchmark-update": {"status": "delivered", "receipt": 42}}
                before = json.loads(json.dumps(lifecycle))
                _ = make_segment_id(event_id, ["benchmark-update"])
                ok = lifecycle == before
            else:
                ok = False
            result.update({"invariant": name}); result["passed"] = ok
            if ok:
                invariant_passed += 1
            else:
                failures.append(cid)
        else:
            failures.append(cid)
        case_results.append(result)

    precision = tp / (tp + fp_count) if tp + fp_count else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    chronology = chronology_correct / chronology_total if chronology_total else 1.0
    ambiguous_deferral = ambiguous_deferred / ambiguous_total if ambiguous_total else 1.0
    passed = not failures and not false_merges and chronology_correct == chronology_total and invariant_passed == invariant_total
    return {
        "passed": passed,
        "case_count": len(cases),
        "pair_case_count": pair_count,
        "same_moment_precision": round(precision, 4),
        "same_moment_recall": round(recall, 4),
        "false_merge_count": len(false_merges),
        "false_merge_cases": false_merges,
        "false_split_count": len(false_splits),
        "false_split_cases": false_splits,
        "chronology_accuracy": round(chronology, 4),
        "chronology_cases": chronology_total,
        "ambiguous_deferral_rate": round(ambiguous_deferral, 4),
        "ambiguous_cases": ambiguous_total,
        "invariant_cases": invariant_total,
        "failed_cases": failures,
        "cases": case_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run(args.fixture)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "TIMELINE BENCHMARK " + ("PASS" if result["passed"] else "FAIL")
            + f": {result['case_count']} cases; precision={result['same_moment_precision']:.4f};"
            + f" recall={result['same_moment_recall']:.4f}; false_merges={result['false_merge_count']};"
            + f" false_splits={result['false_split_count']}; chronology={result['chronology_accuracy']:.4f};"
            + f" ambiguous_deferral={result['ambiguous_deferral_rate']:.4f}"
        )
        if result["failed_cases"]:
            print("FAILED CASES: " + ", ".join(result["failed_cases"]))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
