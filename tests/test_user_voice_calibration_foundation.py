from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from app.channel_style_rewrite import MAX_STYLE_EXAMPLES, StyleProfile, style_fidelity_failures
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
from app import user_voice_calibration_state_compat as state_compat

ROOT = Path(__file__).parents[1]


def _record(index: int, category: str = "SHORT_REACTION", *, candidate: str = "جونگهان امروز اومد.", final: str = "جونگهان امروز اومد. 🩷", factual: str = "جونگهان امروز اومد.", traceable: bool = True, conflict: bool = False):
    return build_calibration_record(
        update_id=f"u-{index}",
        event_id="evt:1",
        segment_id=f"seg:{index}",
        factual_text=factual,
        shadow_candidate=candidate,
        final_user_text=final,
        content_type=category,
        traceable=traceable,
        translation_conflict=conflict,
        review_action="copy",
        created_at="2026-08-15T00:00:00+00:00",
    )


class EditClassificationTests(unittest.TestCase):
    def test_factual_corrections_excluded_from_style_learning(self):
        record = _record(
            1,
            "FACTUAL_INFORMATION",
            factual="جونگهان ساعت 19:30 اومد.",
            candidate="جونگهان ساعت 19:30 اومد.",
            final="جونگهان ساعت 20:30 اومد.",
        )
        self.assertFalse(record.eligible_for_learning)
        self.assertIn("factual_correction", record.labels)
        self.assertFalse(record.fidelity_passed)

    def test_mistranslation_correction_is_excluded_from_style_learning(self):
        record = _record(
            101,
            "FACTUAL_INFORMATION",
            factual="جونگهان ساعت 19:30 اومد.",
            candidate="جونگهان ساعت 20:30 اومد.",
            final="جونگهان ساعت 19:30 اومد.",
        )
        self.assertTrue(record.fidelity_passed)
        self.assertFalse(record.eligible_for_learning)
        self.assertIn("factual_correction", record.labels)
        self.assertNotIn("style_preference", record.labels)

    def test_style_only_edit_is_eligible(self):
        record = _record(2)
        self.assertTrue(record.eligible_for_learning)
        self.assertIn("style_preference", record.labels)
        self.assertIn("emoji_symbol_preference", record.labels)
        self.assertTrue(record.fidelity_passed)

    def test_meaningful_formatting_preference_is_eligible(self):
        text = "جونگهان امروز اومد و خندید."
        record = _record(102, factual=text, candidate=text, final="جونگهان امروز اومد\nو خندید.")
        self.assertTrue(record.eligible_for_learning)
        self.assertIn("formatting_preference", record.labels)
        self.assertNotIn("unclassified", record.labels)

    def test_ambiguous_untraceable_edit_is_excluded(self):
        record = _record(3, traceable=False)
        self.assertFalse(record.eligible_for_learning)
        self.assertIn("ambiguous", record.labels)

    def test_translation_conflict_is_excluded(self):
        record = _record(4, conflict=True)
        self.assertFalse(record.eligible_for_learning)
        self.assertIn("ambiguous", record.labels)

    def test_exact_user_acceptance_is_eligible_style_signal(self):
        record = _record(5, candidate="جونگهان امروز اومد.", final="جونگهان امروز اومد.")
        self.assertTrue(record.eligible_for_learning)
        self.assertIn("style_preference", record.labels)

    def test_typo_only_fix_is_not_promoted(self):
        record = _record(6, candidate="جونگهان امروز اومدد.", final="جونگهان امروز اومد.")
        self.assertFalse(record.eligible_for_learning)
        self.assertIn("unclassified", record.labels)

    def test_fingerprint_metadata_contains_no_message_bodies(self):
        record = _record(7)
        payload = record.metadata()
        self.assertFalse(payload["text_persisted"])
        self.assertNotIn("factual_text", payload)
        self.assertNotIn("candidate_text", payload)
        self.assertNotIn("final_user_text", payload)


class PreferenceAggregationTests(unittest.TestCase):
    def test_one_off_edit_does_not_create_global_preference(self):
        signals = derive_preference_signals([_record(10)])
        self.assertFalse(any(signal.scope == "global" for signal in signals))

    def test_repeated_global_preference_can_be_recognized(self):
        records = [
            _record(11, "SHORT_REACTION"),
            _record(12, "WEVERSE_POST"),
            _record(13, "INTERVIEW"),
        ]
        signals = derive_preference_signals(records)
        self.assertTrue(any(signal.scope == "global" and signal.feature == "emoji" for signal in signals))

    def test_category_preference_remains_category_scoped(self):
        records = [_record(20 + i, "SHORT_REACTION") for i in range(3)]
        signals = derive_preference_signals(records)
        self.assertTrue(any(signal.scope == "category" and signal.category == "SHORT_REACTION" for signal in signals))
        self.assertFalse(any(signal.scope == "global" for signal in signals))

    def test_ai_like_pattern_requires_repeated_evidence(self):
        one = _record(30, candidate="لازم به ذکر است جونگهان امروز اومد.", final="جونگهان امروز اومد.")
        self.assertFalse(any(signal.scope == "ai_pattern" for signal in derive_preference_signals([one])))
        records = [
            _record(31, "SHORT_REACTION", candidate="لازم به ذکر است جونگهان امروز اومد.", final="جونگهان امروز اومد."),
            _record(32, "WEVERSE_POST", candidate="لازم به ذکر است جونگهان امروز اومد.", final="جونگهان امروز اومد."),
            _record(33, "INTERVIEW", candidate="لازم به ذکر است جونگهان امروز اومد.", final="جونگهان امروز اومد."),
        ]
        self.assertTrue(any(signal.scope == "ai_pattern" for signal in derive_preference_signals(records)))

    def test_holdout_data_is_not_in_calibration_set(self):
        records = [_record(40 + i, "SHORT_REACTION" if i % 2 else "WEVERSE_POST") for i in range(30)]
        calibration, holdout = deterministic_record_split(records)
        self.assertTrue(calibration)
        self.assertTrue(holdout)
        self.assertTrue({x.record_id for x in calibration}.isdisjoint({x.record_id for x in holdout}))
        self.assertEqual(len(calibration) + len(holdout), len(records))


class RankingAndReversibilityTests(unittest.TestCase):
    def _signals(self):
        return derive_preference_signals(
            [_record(50, "SHORT_REACTION"), _record(51, "WEVERSE_POST"), _record(52, "INTERVIEW")]
        )

    def test_calibration_ranking_changes_are_bounded(self):
        examples = [
            RetrievedStyleExample(str(i), f"نمونه {i} {'🩷' if i % 2 else ''}", "SHORT_REACTION", "fa", "2024", 5.0 - i * 0.1, ["base"])
            for i in range(8)
        ]
        original = {item.example_id: item.score for item in examples}
        result = calibrate_example_ranking(examples, content_type="SHORT_REACTION", signals=self._signals(), limit=8)
        self.assertLessEqual(len(result), MAX_STYLE_EXAMPLES)
        for item in result:
            self.assertLessEqual(abs(item.score - original[item.example_id]), MAX_RANKING_DELTA + 1e-9)

    def test_ranking_never_mutates_example_text(self):
        examples = [RetrievedStyleExample("a", "متن تاریخی 🩷", "SHORT_REACTION", "fa", "2024", 1.0, [])]
        result = calibrate_example_ranking(examples, content_type="SHORT_REACTION", signals=self._signals())
        self.assertEqual(result[0].text, examples[0].text)

    def test_snapshot_is_reversible(self):
        snapshot = make_calibration_snapshot(
            [_record(60, "SHORT_REACTION"), _record(61, "WEVERSE_POST"), _record(62, "INTERVIEW")],
            previous_weights={"existing": 0.12},
            previous_snapshot_id="uvcs:old",
        )
        self.assertEqual(rollback_weights(snapshot), {"existing": 0.12})
        self.assertEqual(snapshot.previous_snapshot_id, "uvcs:old")
        self.assertTrue(all(abs(value) <= MAX_RANKING_DELTA for value in snapshot.new_weights.values()))

    def test_auto_learn_remains_false(self):
        self.assertFalse(AUTO_LEARN)
        self.assertEqual(VOICE_CALIBRATION_MODE, "shadow")
        payload = bounded_state_payload([_record(63)])
        self.assertFalse(payload["auto_learn"])
        self.assertFalse(payload["text_persisted"])

    def test_bounded_state_payload_has_no_canonical_text(self):
        payload = bounded_state_payload([_record(64)])
        self.assertTrue(record_bodies_are_absent(payload))


class FidelityHardGateTests(unittest.TestCase):
    def assertRejected(self, factual: str, changed: str):
        self.assertTrue(style_fidelity_failures(factual, changed), (factual, changed))

    def test_names_preserved(self):
        self.assertRejected("جونگهان امروز اومد.", "جاشوآ امروز اومد.")

    def test_numbers_preserved(self):
        self.assertRejected("جونگهان ساعت 19:30 اومد.", "جونگهان ساعت 20:30 اومد.")

    def test_negation_preserved(self):
        self.assertRejected("جونگهان امروز نیومد.", "جونگهان امروز اومد.")

    def test_speaker_preserved(self):
        self.assertRejected("جونگهان: سلام\nجاشوآ: سلام", "جاشوآ: سلام\nجونگهان: سلام")

    def test_chronology_preserved(self):
        self.assertRejected("اول جونگهان اومد بعد جاشوآ", "بعد جونگهان اومد اول جاشوآ")

    def test_unsupported_relationship_inference_rejected(self):
        self.assertRejected("جونگهان امروز اومد.", "جونگهان امروز با دوست پسرش اومد.")

    def test_historical_fact_leakage_remains_blocked(self):
        examples = [RetrievedStyleExample("hist", "جونگهان گفت رفت ژاپن", "OTHER", "fa", "2024", 1.0, [])]
        failures = style_fidelity_failures("جونگهان گفت رفت بوسان", "جونگهان گفت رفت ژاپن", examples)
        self.assertTrue(any(item.startswith("historical_example_exclusive_token:") for item in failures))

    def test_before_after_comparison_never_overrides_fidelity(self):
        profile = StyleProfile("OTHER", "OTHER", 20, "factual", 30.0, 0.1, 0.0, 0.1, 0.1, 0.0, True)
        result = compare_shadow_style(
            "جونگهان ساعت 19:30 اومد.",
            "جونگهان ساعت 19:30 اومد.",
            "جونگهان ساعت 20:30 اومد.",
            profile=profile,
        )
        self.assertFalse(result["calibrated_fidelity_passed"])
        self.assertGreater(result["unsupported_additions"], 0)


class DurableStateTests(unittest.TestCase):
    def test_calibration_metadata_is_reversible_and_sanitized(self):
        from app import user_voice_calibration as calibration

        record = _record(70)
        snapshot = make_calibration_snapshot([record], previous_weights={"x": 0.1})
        raw = {"voice_calibration": bounded_state_payload([record], snapshot)}
        clean = state_compat.sanitize_voice_state(raw, calibration)
        self.assertFalse(clean["auto_learn"])
        self.assertFalse(clean["text_persisted"])
        self.assertTrue(record_bodies_are_absent(clean))
        self.assertIn(record.record_id, clean["records"])

    def test_malicious_full_text_keys_are_dropped(self):
        from app import user_voice_calibration as calibration

        record = _record(71).metadata()
        record["factual_text"] = "do not persist me"
        raw = {"voice_calibration": {"records": {record["record_id"]: record}}}
        clean = state_compat.sanitize_voice_state(raw, calibration)
        self.assertTrue(record_bodies_are_absent(clean))


class RepositoryRegressionTests(unittest.TestCase):
    def test_review_only_remains_true(self):
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        self.assertIs(settings["runtime"]["review_only"], True)

    def test_24_sources_remain_configured_with_only_verified_suspension_disabled(self):
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
        self.assertEqual(len(sources), 24)
        disabled = [item for item in sources if not bool(item.get("enabled", True))]
        self.assertEqual(
            disabled,
            [
                {
                    "handle": "flamehanie",
                    "label": "flamehanie",
                    "enabled": False,
                    "disabled_reason": "x_suspended_2026-08-31",
                    "priority": 10,
                    "include_replies": True,
                    "mode": "full_feed",
                }
            ],
        )

    def test_realtime_shadow_mode_remains_off_by_default(self):
        from app.realtime_ingest import realtime_shadow_enabled

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(realtime_shadow_enabled())

    def test_no_corpus_rewrite_code(self):
        source = (ROOT / "app" / "user_voice_calibration.py").read_text(encoding="utf-8")
        self.assertNotIn("write_text(", source)
        self.assertNotIn("write_bytes(", source)
        self.assertNotIn("DELETE FROM channel_style_examples", source)

    def test_no_telegram_authority_change_in_calibration_module(self):
        source = (ROOT / "app" / "user_voice_calibration.py").read_text(encoding="utf-8")
        for forbidden in ("send_message(", "mark_seen(", "mark_delivered", "telegram_message_id", "delivery_key"):
            self.assertNotIn(forbidden, source)

    def test_event_timeline_translation_phase23_receipts_and_media_are_not_owned(self):
        source = (ROOT / "app" / "user_voice_calibration.py").read_text(encoding="utf-8")
        for forbidden in (
            "shadow_group_updates(", "fuse_evidence_items(", "pending_delivery", "telegram_receipt",
            "MediaManager", "send_media(", "Phase 3", "recovery_cursor",
        ):
            self.assertNotIn(forbidden, source)

    def test_private_runtime_only_installs_metadata_compatibility(self):
        source = (ROOT / "app" / "event_fusion_private_runtime.py").read_text(encoding="utf-8")
        self.assertIn("user_voice_calibration_state_compat.install", source)
        self.assertNotIn("calibrate_example_ranking(", source)
        self.assertIn("return await current(self, updates, force=force)", source)

    def test_fanfic_import_remains_independent(self):
        code = (
            "import sys; import app.fic_digest; "
            "assert 'app.user_voice_calibration' not in sys.modules; "
            "assert 'app.channel_style_rewrite' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_paid_or_new_database_dependency(self):
        source = (ROOT / "app" / "user_voice_calibration.py").read_text(encoding="utf-8").casefold()
        for forbidden in ("supabase", "redis", "pinecone", "qdrant", "weaviate", "paid api", "openai"):
            self.assertNotIn(forbidden, source)
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
        for forbidden in ("supabase", "redis", "pinecone", "qdrant", "weaviate"):
            self.assertNotIn(forbidden, requirements)

    def test_style_example_limit_is_unchanged(self):
        self.assertEqual(MAX_STYLE_EXAMPLES, 5)

    def test_existing_translation_feedback_is_not_auto_learning(self):
        source = (ROOT / "app" / "channel_style.py").read_text(encoding="utf-8")
        self.assertIn("if not confirmed", source)
        calibration_source = (ROOT / "app" / "user_voice_calibration.py").read_text(encoding="utf-8")
        self.assertNotIn("add_confirmed_feedback(", calibration_source)


if __name__ == "__main__":
    unittest.main()
