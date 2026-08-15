from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.translation_fusion import TranslationEvidence, fuse_evidence_items

ROOT = Path(__file__).resolve().parents[1]


def _evidence(case: dict[str, Any], raw: dict[str, Any]) -> TranslationEvidence:
    return TranslationEvidence(
        update_id=str(raw["update_id"]),
        source=str(raw.get("source", "hani_berry_1004")),
        source_language=str(raw.get("source_language", "en")),
        evidence_kind=str(raw.get("evidence_kind", "direct_translation")),
        original_text=str(raw.get("original_text", "")),
        candidate_text=str(raw.get("candidate_text", "")),
        event_id=str(case.get("event_id") or f"evt:bench:{case.get('id', '')}"),
        segment_id=str(case.get("segment_id") or f"seg:bench:{case.get('id', '')}"),
        relationship=str(raw.get("relationship", "same_moment")),
        relationship_confidence=float(raw.get("relationship_confidence", 0.95) or 0),
        evidence_strength=float(raw.get("evidence_strength", 0.9) or 0),
        matching_signals=tuple(map(str, raw.get("matching_signals", []))),
        conflicts=tuple(map(str, raw.get("conflicts", []))),
        media_reference_ids=tuple(map(str, raw.get("media_reference_ids", []))),
    )


def _case_ok(result, expected: dict[str, Any]) -> bool:
    text = result.fused_factual_text
    return (
        result.backbone_update_id == str(expected.get("backbone_update_id", ""))
        and list(result.complementary_update_ids) == list(expected.get("complementary_update_ids", []))
        and list(result.conflict_update_ids) == list(expected.get("conflict_update_ids", []))
        and result.review_required is bool(expected.get("review_required", False))
        and result.fidelity_status == str(expected.get("fidelity_status", ""))
        and all(str(fragment) in text for fragment in expected.get("required_fragments", []))
        and all(str(fragment) not in text for fragment in expected.get("forbidden_fragments", []))
    )


def run(path: Path | None = None) -> dict[str, Any]:
    path = path or ROOT / "data" / "translation_fusion_benchmark.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list):
        raise ValueError("benchmark cases must be a list")

    case_results = []
    required_total = required_hit = 0
    output_line_total = supported_line_total = 0
    unsupported_additions = 0
    tagged_totals: dict[str, int] = {}
    tagged_passed: dict[str, int] = {}

    for case in cases:
        if not isinstance(case, dict):
            continue
        evidence = [_evidence(case, raw) for raw in case.get("evidence", []) if isinstance(raw, dict)]
        result = fuse_evidence_items(
            evidence,
            event_id=str(case.get("event_id") or f"evt:bench:{case.get('id', '')}"),
            segment_id=str(case.get("segment_id") or f"seg:bench:{case.get('id', '')}"),
        )
        expected = case.get("expect", {}) if isinstance(case.get("expect"), dict) else {}
        passed = _case_ok(result, expected)

        required = [str(item) for item in expected.get("required_fragments", [])]
        required_total += len(required)
        required_hit += sum(fragment in result.fused_factual_text for fragment in required)

        allowed_lines = {
            line.strip()
            for item in evidence
            for line in item.candidate_text.splitlines()
            if line.strip()
        }
        output_lines = [line.strip() for line in result.fused_factual_text.splitlines() if line.strip()]
        output_line_total += len(output_lines)
        supported = sum(line in allowed_lines for line in output_lines)
        supported_line_total += supported
        unsupported_additions += len(output_lines) - supported

        for tag in map(str, expected.get("metric_tags", [])):
            tagged_totals[tag] = tagged_totals.get(tag, 0) + 1
            if passed:
                tagged_passed[tag] = tagged_passed.get(tag, 0) + 1

        case_results.append({
            "id": str(case.get("id", "")),
            "passed": passed,
            "backbone_update_id": result.backbone_update_id,
            "fidelity_status": result.fidelity_status,
            "review_required": result.review_required,
            "conflict_update_ids": list(result.conflict_update_ids),
        })

    def metric(tag: str) -> float:
        total = tagged_totals.get(tag, 0)
        return round(tagged_passed.get(tag, 0) / total, 4) if total else 1.0

    metrics = {
        "factual_precision": round(supported_line_total / output_line_total, 4) if output_line_total else 1.0,
        "factual_recall": round(required_hit / required_total, 4) if required_total else 1.0,
        "unsupported_addition_count": unsupported_additions,
        "contradiction_preservation": metric("conflict"),
        "speaker_attribution_accuracy": metric("speaker"),
        "name_accuracy": metric("name"),
        "number_date_accuracy": metric("number_date"),
        "negation_accuracy": metric("negation"),
        "review_required_correctness": metric("review"),
    }
    passed = (
        len(case_results) >= 30
        and all(item["passed"] for item in case_results)
        and metrics["unsupported_addition_count"] == 0
        and metrics["factual_precision"] == 1.0
        and metrics["factual_recall"] == 1.0
        and metrics["contradiction_preservation"] == 1.0
        and metrics["speaker_attribution_accuracy"] == 1.0
        and metrics["name_accuracy"] == 1.0
        and metrics["number_date_accuracy"] == 1.0
        and metrics["negation_accuracy"] == 1.0
        and metrics["review_required_correctness"] == 1.0
    )
    return {
        "passed": passed,
        "case_count": len(case_results),
        **metrics,
        "failed_case_ids": [item["id"] for item in case_results if not item["passed"]],
        "cases": case_results,
    }


def main() -> int:
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
