"""Deterministic benchmark for the shadow User-Voice Calibration foundation.

This benchmark proves safety/mechanics only.  It deliberately does not claim that
synthetic fixtures prove the user's real voice quality.  Real edit evidence is a
separate production-observation requirement.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.channel_style_rewrite import StyleProfile, style_fidelity_failures
from app.channel_style_runtime import RetrievedStyleExample
from app.user_voice_calibration import (
    AUTO_LEARN,
    MAX_RANKING_DELTA,
    VOICE_CALIBRATION_MODE,
    bounded_state_payload,
    build_calibration_record,
    calibrate_example_ranking,
    compare_shadow_style,
    derive_preference_signals,
    deterministic_record_split,
    make_calibration_snapshot,
    record_bodies_are_absent,
    rollback_weights,
)

ROOT = Path(__file__).parents[1]


def _record(index: int, category: str, candidate: str, final: str, *, factual: str = "جونگهان امروز اومد."):
    return build_calibration_record(
        update_id=f"bench-{index}",
        event_id="evt:benchmark",
        segment_id=f"seg:{index}",
        factual_text=factual,
        shadow_candidate=candidate,
        final_user_text=final,
        content_type=category,
        traceable=True,
        translation_conflict=False,
        review_action="copy",
        created_at="2026-08-15T00:00:00+00:00",
    )


def _real_corpus_holdout_ids() -> tuple[list[str], list[str]]:
    """Use real corpus IDs for split-isolation coverage, never as factual labels."""
    ids: list[str] = []
    for path in sorted((ROOT / "data" / "channel_style").glob("examples-*.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                example_id = str(row.get("example_id", "")).strip()
                if example_id:
                    ids.append(example_id)
                if len(ids) >= 80:
                    break
        if len(ids) >= 80:
            break
    calibration: list[str] = []
    holdout: list[str] = []
    import hashlib
    for example_id in sorted(set(ids)):
        bucket = int.from_bytes(hashlib.sha256(example_id.encode("utf-8")).digest()[:4], "big") % 5
        (holdout if bucket == 0 else calibration).append(example_id)
    return calibration, holdout


def run() -> dict:
    cases: list[tuple[str, bool]] = []

    style_only = _record(1, "SHORT_REACTION", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷")
    cases.append(("style-only edit eligible", style_only.eligible_for_learning))

    factual = build_calibration_record(
        update_id="bench-fact",
        factual_text="جونگهان ساعت 19:30 اومد.",
        shadow_candidate="جونگهان ساعت 19:30 اومد.",
        final_user_text="جونگهان ساعت 20:30 اومد.",
        content_type="FACTUAL_INFORMATION",
        traceable=True,
    )
    cases.append(("factual correction excluded", not factual.eligible_for_learning and "factual_correction" in factual.labels))

    ambiguous = build_calibration_record(
        update_id="bench-amb",
        factual_text="جونگهان امروز اومد.",
        shadow_candidate="جونگهان امروز اومد.",
        final_user_text="جونگهان امروز اومد. 🩷",
        content_type="OTHER",
        traceable=False,
    )
    cases.append(("ambiguous edit excluded", not ambiguous.eligible_for_learning and "ambiguous" in ambiguous.labels))

    repeated = [
        _record(10, "SHORT_REACTION", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷"),
        _record(11, "WEVERSE_POST", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷"),
        _record(12, "INTERVIEW", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷"),
    ]
    signals = derive_preference_signals(repeated)
    cases.append(("repeated global preference recognized", any(x.scope == "global" and x.feature == "emoji" for x in signals)))

    one_off = derive_preference_signals([style_only])
    cases.append(("one-off does not become global", not any(x.scope == "global" for x in one_off)))

    category_records = [
        _record(20, "SHORT_REACTION", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷"),
        _record(21, "SHORT_REACTION", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷"),
        _record(22, "SHORT_REACTION", "جونگهان امروز اومد.", "جونگهان امروز اومد. 🩷"),
    ]
    category_signals = derive_preference_signals(category_records)
    cases.append(("category preference scoped", any(x.scope == "category" and x.category == "SHORT_REACTION" for x in category_signals)))
    cases.append(("category preference not global", not any(x.scope == "global" for x in category_signals)))

    ai_records = [
        _record(30 + i, category, "لازم به ذکر است جونگهان امروز اومد.", "جونگهان امروز اومد.")
        for i, category in enumerate(("OTHER", "WEVERSE_POST", "INTERVIEW"))
    ]
    ai_signals = derive_preference_signals(ai_records)
    cases.append(("AI-like pattern needs repeated evidence", any(x.scope == "ai_pattern" for x in ai_signals)))
    cases.append(("single AI-like removal not promoted", not any(x.scope == "ai_pattern" for x in derive_preference_signals(ai_records[:1]))))

    examples = [
        RetrievedStyleExample("a", "جونگهان امروز اومد 🩷", "SHORT_REACTION", "fa", "2024", 5.0, ["base"]),
        RetrievedStyleExample("b", "جونگهان امروز اومد", "SHORT_REACTION", "fa", "2024", 5.0, ["base"]),
        RetrievedStyleExample("c", "آپدیت رسمی منتشر شد.", "FACTUAL_INFORMATION", "fa", "2024", 4.0, ["base"]),
        RetrievedStyleExample("d", "جونگهان اومد 😭", "SHORT_REACTION", "fa", "2024", 3.5, ["base"]),
        RetrievedStyleExample("e", "جونگهان برگشت.", "SHORT_REACTION", "fa", "2024", 3.0, ["base"]),
        RetrievedStyleExample("f", "جونگهان امروز برگشت.", "SHORT_REACTION", "fa", "2024", 2.5, ["base"]),
    ]
    ranked = calibrate_example_ranking(examples, content_type="SHORT_REACTION", signals=signals, limit=5)
    original_scores = {x.example_id: x.score for x in examples}
    cases.append(("ranking stays bounded to five", len(ranked) <= 5))
    cases.append(("ranking deltas bounded", all(abs(x.score - original_scores[x.example_id]) <= MAX_RANKING_DELTA + 1e-9 for x in ranked)))

    calibration_set, holdout_set = deterministic_record_split(repeated + category_records + ai_records)
    calibration_ids = {x.record_id for x in calibration_set}
    holdout_ids = {x.record_id for x in holdout_set}
    cases.append(("record holdout isolated", calibration_ids.isdisjoint(holdout_ids)))

    corpus_cal, corpus_holdout = _real_corpus_holdout_ids()
    cases.append(("real corpus holdout IDs isolated", bool(corpus_cal) and bool(corpus_holdout) and set(corpus_cal).isdisjoint(corpus_holdout)))

    snapshot = make_calibration_snapshot(repeated, previous_weights={"existing": 0.1})
    cases.append(("snapshot reversible", rollback_weights(snapshot) == {"existing": 0.1}))
    payload = bounded_state_payload(repeated, snapshot)
    cases.append(("state persists no bodies", payload.get("text_persisted") is False and record_bodies_are_absent(payload)))
    cases.append(("auto-learn remains false", AUTO_LEARN is False and payload.get("auto_learn") is False))
    cases.append(("mode remains shadow", VOICE_CALIBRATION_MODE == "shadow"))

    leak_failures = style_fidelity_failures(
        "جونگهان گفت رفت بوسان",
        "جونگهان گفت رفت ژاپن",
        [RetrievedStyleExample("hist", "جونگهان گفت رفت ژاپن", "OTHER", "fa", "2024", 1.0, [])],
    )
    cases.append(("historical fact leakage blocked", any(x.startswith("historical_example_exclusive_token:") for x in leak_failures)))

    fidelity_cases = {
        "name": ("جونگهان امروز اومد.", "جاشوآ امروز اومد."),
        "number": ("جونگهان ساعت 19:30 اومد.", "جونگهان ساعت 20:30 اومد."),
        "negation": ("جونگهان امروز نیومد.", "جونگهان امروز اومد."),
        "speaker": ("جونگهان: سلام\nجاشوآ: سلام", "جاشوآ: سلام\nجونگهان: سلام"),
        "chronology": ("اول جونگهان اومد بعد جاشوآ", "بعد جونگهان اومد اول جاشوآ"),
        "relationship": ("جونگهان امروز اومد.", "جونگهان امروز با دوست پسرش اومد."),
    }
    for name, (source, changed) in fidelity_cases.items():
        cases.append((f"factual lock {name}", bool(style_fidelity_failures(source, changed))))

    profile = StyleProfile(
        key="SHORT_REACTION",
        content_type="SHORT_REACTION",
        example_count=20,
        register="reaction",
        median_chars=24.0,
        multiline_pct=0.1,
        dialogue_pct=0.0,
        emoji_pct=0.7,
        reaction_pct=0.7,
        formal_connector_pct=0.0,
        supported=True,
    )
    comparison = compare_shadow_style(
        "جونگهان امروز اومد.",
        "جونگهان امروز اومد.",
        "جونگهان امروز اومد. 🩷",
        profile=profile,
    )
    cases.append(("before/after keeps fidelity", comparison["old_fidelity_passed"] and comparison["calibrated_fidelity_passed"]))
    cases.append(("unsupported additions zero", comparison["unsupported_additions"] == 0))

    failures = [name for name, passed in cases if not passed]
    report = {
        "benchmark": "user_voice_calibration_foundation",
        "synthetic_case_count": len(cases),
        "passed": len(cases) - len(failures),
        "failed": len(failures),
        "failures": failures,
        "real_user_edit_evidence": "not asserted by this benchmark",
        "real_corpus_split": {"calibration_ids": len(corpus_cal), "holdout_ids": len(corpus_holdout)},
        "calibration_record_split": {"calibration": len(calibration_set), "holdout": len(holdout_set)},
        "unsupported_additions": comparison["unsupported_additions"],
        "historical_leakage": comparison["historical_leakage"],
        "old_style_score": comparison["old_style_score"],
        "calibrated_style_score": comparison["calibrated_style_score"],
        "auto_learn": AUTO_LEARN,
        "mode": VOICE_CALIBRATION_MODE,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if failures:
        raise SystemExit(1)
    return report


if __name__ == "__main__":
    run()
