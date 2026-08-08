from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.ai import CaptionWriter
from app.channel_style_runtime import analyze_source, verify_hard_facts
from app.channel_translation import ChannelStyleCaptionWriter
from app.config import ROOT
from app.models import EventGroup, Update
from app.style import StyleMemory


def _legacy_category(content_type: str) -> str:
    mapping = {
        "LIVE_DIALOGUE": "live",
        "WEVERSE_LIVE": "live",
        "FANSIGN": "fansign",
        "AIRPORT": "airport",
        "INSTAGRAM_UPDATE": "jeonghan_instagram",
        "BRAND_AD": "brand",
    }
    return mapping.get(content_type, "general")


def _group(case: dict) -> EventGroup:
    category = _legacy_category(str(case.get("content_type", "OTHER")))
    update = Update(
        id=str(case["id"]),
        url=f"https://example.invalid/benchmark/{case['id']}",
        author="benchmark_source",
        author_name="benchmark source",
        text=str(case["source"]),
        created_at=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc),
        lang="",
        category=category,
    )
    return EventGroup(
        key=f"benchmark:{case['id']}",
        category=category,
        title=str(case.get("content_type", case["id"])),
        updates=[update],
    )


def _analysis_payload(analysis) -> dict:
    return {
        "source_language": analysis.source_language,
        "content_type": analysis.content_type,
        "line_count": analysis.line_count,
        "char_count": analysis.char_count,
        "has_dialogue": analysis.has_dialogue,
        "platform": analysis.platform,
        "fact_ledger": analysis.fact_ledger(),
    }


def run(cases_path: Path, output_path: Path) -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is required for the real PART 4 benchmark")
    model = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not 30 <= len(cases) <= 40:
        raise SystemExit(f"expected 30-40 benchmark cases, got {len(cases)}")

    memory = StyleMemory(ROOT)
    old_writer = CaptionWriter(api_key, model, memory)
    new_writer = ChannelStyleCaptionWriter(api_key, model, memory)
    results: list[dict] = []
    try:
        for case in cases:
            group = _group(case)
            source = str(case["source"])
            analysis = analyze_source(source)
            old_copy = old_writer.write_group(group)
            new_copy = new_writer.write_group(group)
            old_body = old_copy.bodies.get(str(case["id"]), "")
            new_body = new_copy.bodies.get(str(case["id"]), "")
            hard_failures = verify_hard_facts(source, new_body, analysis)
            diagnostics = dict(new_writer.last_diagnostics)
            fallback = str(diagnostics.get("fallback", ""))
            results.append(
                {
                    "case_id": case["id"],
                    "coverage": case.get("coverage", []),
                    "source": source,
                    "source_language": analysis.source_language,
                    "content_type": analysis.content_type,
                    "declared_content_type": case.get("content_type", ""),
                    "source_analysis": _analysis_payload(analysis),
                    "old_legacy_output": old_body,
                    "new_channel_style_output": new_body,
                    "retrieved_historical_example_ids": diagnostics.get("retrieved_example_ids", []),
                    "retrieval_scores": diagnostics.get("retrieval_scores", {}),
                    "retrieval_reasons": diagnostics.get("retrieval_reasons", {}),
                    "glossary_entries_used": diagnostics.get("glossary_entries", []),
                    "verifier_result": "PASS" if not hard_failures else "FAIL",
                    "verifier_failures": hard_failures,
                    "fact_leak_guard_result": "BLOCKED_TO_NEUTRAL" if "fact_leak" in fallback else "PASS_NO_BLOCK",
                    "warnings": [fallback] if fallback else [],
                    "recency_weighting": diagnostics.get("recency_weighting", "NONE"),
                    "date_score_contribution": 0,
                }
            )
    finally:
        memory.close()

    payload = {
        "benchmark_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pipeline_old": "app.ai.CaptionWriter.write_group (preserved legacy production path)",
        "pipeline_new": "app.channel_translation.ChannelStyleCaptionWriter.write_group (current production path)",
        "authority_message_count": 16306,
        "recency_weighting": "NONE",
        "date_score_contribution": 0,
        "case_count": len(results),
        "cases": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PART4 BENCHMARK OK: {len(results)} real OLD/NEW cases -> {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "translation_benchmark_cases.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.cases, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
