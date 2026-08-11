from __future__ import annotations

"""Small live gate for the exact production translation writer.

The full 30-40 case benchmark is an editorial/human certification artifact and is
kept for manual workflow dispatch. Pull requests need a bounded, actionable check:
one English, one Korean, and one Japanese case through the exact production V2
writer, with deterministic fidelity/publishability guards.
"""

import json
import os
from pathlib import Path

from app.channel_entities import entity_failures
from app.channel_style_safety import validate_production_style_memory
from app.channel_translation import ChannelStyleCaptionWriter
from app.channel_translation_v2_install import install_direct_v2
from app.channel_style_runtime import analyze_source, verify_hard_facts
from app.config import ROOT, Settings
from app.style import StyleMemory
from app.translation_safety import natural_persian_failures, semantic_quality_failures
from tools.run_translation_benchmark import _group

SMOKE_CASE_IDS = ("B02", "B03", "B06")
CASES_PATH = ROOT / "data" / "translation_benchmark_cases.json"


def _load_smoke_cases(path: Path = CASES_PATH) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    by_id = {str(item.get("id")): item for item in payload if isinstance(item, dict)}
    missing = [case_id for case_id in SMOKE_CASE_IDS if case_id not in by_id]
    if missing:
        raise SystemExit(f"missing translation smoke cases: {','.join(missing)}")
    return [by_id[case_id] for case_id in SMOKE_CASE_IDS]


def _case_failures(case: dict, writer: ChannelStyleCaptionWriter) -> list[str]:
    group = _group(case)
    update = group.updates[0]
    source = update.translation_source()
    copy = writer.write_group(group)
    body = str(copy.bodies.get(update.id, "")).strip()
    diagnostics = dict(getattr(writer, "last_diagnostics", {}) or {})
    failures: list[str] = []

    if not body:
        failures.append("empty_output")
    fallback = str(diagnostics.get("fallback", ""))
    if fallback:
        failures.append(f"fallback:{fallback}")
    if not str(diagnostics.get("output_mode", "")).startswith("styled_direct"):
        failures.append(f"output_mode:{diagnostics.get('output_mode', 'missing')}")
    if getattr(writer, "last_manual_review", {}):
        failures.append("manual_review_required")

    analysis = analyze_source(source)
    failures.extend(f"hard:{item}" for item in verify_hard_facts(source, body, analysis))
    failures.extend(f"entity:{item}" for item in entity_failures(source, body))
    failures.extend(f"semantic:{item}" for item in semantic_quality_failures(update, body))
    failures.extend(f"natural:{item}" for item in natural_persian_failures(update, body))
    return failures


def main() -> int:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is required for production translation smoke")

    settings = Settings.load(require_secrets=False)
    model = os.environ.get("GEMINI_MODEL", "").strip() or settings.gemini_model
    memory = StyleMemory(ROOT)
    try:
        ok, reason, indexed = validate_production_style_memory(memory, ROOT)
        if not ok:
            print(f"TRANSLATION SMOKE FAIL: style memory unavailable: {reason}", flush=True)
            return 1
        writer = ChannelStyleCaptionWriter(api_key, model, memory)
        install_direct_v2(writer)
        print(f"TRANSLATION SMOKE: model={model}; style_examples={indexed}", flush=True)
        failed = False
        for case in _load_smoke_cases():
            writer.last_diagnostics = {}
            failures = _case_failures(case, writer)
            if failures:
                failed = True
                print(f"SMOKE {case['id']} FAIL: {' | '.join(failures)}", flush=True)
            else:
                source = _group(case).updates[0].translation_source()
                analysis = analyze_source(source)
                print(
                    f"SMOKE {case['id']} PASS: language={analysis.source_language}; "
                    f"content={analysis.content_type}; output_mode={writer.last_diagnostics.get('output_mode')}",
                    flush=True,
                )
        if failed:
            print("TRANSLATION PRODUCTION SMOKE FAILED", flush=True)
            return 1
        print("TRANSLATION PRODUCTION SMOKE PASS: EN/KO/JA exact production writer", flush=True)
        return 0
    finally:
        memory.close()


if __name__ == "__main__":
    raise SystemExit(main())
