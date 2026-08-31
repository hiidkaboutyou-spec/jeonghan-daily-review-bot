from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from app.channel_style_rewrite import (
    STYLE_REWRITE_MODE,
    _fresh_style_fields,
    _prune_style,
    rewrite_shadow_candidate,
)
from app.direct_style_rules import (
    DEFAULT_AUTHORITY_ORDER,
    DIRECT_STYLE_RULES_MODE,
    DirectStyleEvidence,
    DirectStylePlanner,
    DirectStyleRuleSet,
    StyleDirective,
    select_rotating_symbol,
)
from app.user_voice_calibration import AUTO_LEARN
from tools.run_direct_user_style_rules_benchmark import run as run_direct_style_benchmark

ROOT = Path(__file__).resolve().parents[1]


class EmptyMemory:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")

    def close(self):
        self.conn.close()


class ReplacingProvider:
    name = "lower_priority_fixture"

    def __init__(self, body: str):
        self.body = body

    def rewrite(self, rewrite_input, examples, profile):
        return self.body


def evidence(**overrides) -> DirectStyleEvidence:
    values = {
        "content_type": "OTHER",
        "category": "general",
        "platform": "",
        "account": "",
        "brand": "",
        "date": "",
        "title": "",
        "is_story": False,
        "is_dialogue": False,
        "has_jeonghan": False,
        "ambiguous": False,
    }
    values.update(overrides)
    return DirectStyleEvidence(**values)


class DirectUserStyleRulesFoundationTests(unittest.TestCase):
    def setUp(self):
        self.rules = DirectStyleRuleSet.load()
        self.planner = DirectStylePlanner(self.rules)

    def plan(self, current: DirectStyleEvidence, key: str = "evt:seg", recent=()) -> StyleDirective:
        return self.planner.plan(current, context_key=key, recent_symbols=recent)

    def test_authority_precedence_is_structural(self):
        self.assertEqual(self.rules.authority_order, DEFAULT_AUTHORITY_ORDER)
        self.assertEqual(DEFAULT_AUTHORITY_ORDER[0], "factual_fidelity")
        self.assertLess(DEFAULT_AUTHORITY_ORDER.index("direct_user_style_rules"), DEFAULT_AUTHORITY_ORDER.index("historical_style_examples"))
        self.assertLess(DEFAULT_AUTHORITY_ORDER.index("direct_user_style_rules"), DEFAULT_AUTHORITY_ORDER.index("stable_real_user_edit_preferences"))

    def test_rules_are_config_driven_and_priority_sorted(self):
        self.assertGreaterEqual(len(self.rules.rules), 5)
        self.assertEqual([item.priority for item in self.rules.rules], sorted((item.priority for item in self.rules.rules), reverse=True))

    def test_instagram_feed_header(self):
        plan = self.plan(evidence(content_type="INSTAGRAM_UPDATE", category="instagram", platform="instagram", account="jeonghaniyoo_n"))
        self.assertEqual(plan.rule_id, "jeonghan_instagram_feed")
        self.assertEqual(plan.header, "🧸    #IG ׂ ✧   ﹫ jeonghaniyoo_n")

    def test_instagram_story_is_more_specific_than_feed(self):
        plan = self.plan(evidence(content_type="INSTAGRAM_UPDATE", category="instagram story", platform="instagram", account="jeonghaniyoo_n", is_story=True))
        self.assertEqual(plan.rule_id, "jeonghan_instagram_story")
        self.assertEqual(plan.header, "jeonghaniyoo_n 𐑞✿ྀི instagram story:")
        self.assertFalse(plan.body_prefix)

    def test_story_rule_rejects_other_account(self):
        plan = self.plan(evidence(content_type="INSTAGRAM_UPDATE", platform="instagram", account="other", is_story=True))
        self.assertFalse(plan.applied)

    def test_feed_rule_never_matches_story(self):
        plan = self.plan(evidence(content_type="INSTAGRAM_UPDATE", platform="instagram", account="jeonghaniyoo_n", is_story=True))
        self.assertEqual(plan.rule_id, "jeonghan_instagram_story")

    def test_weverse_uses_current_date(self):
        plan = self.plan(evidence(content_type="WEVERSE_POST", category="weverse", platform="weverse", date="260816"))
        self.assertEqual(plan.rule_id, "weverse_update")
        self.assertIn("-260816🥛", plan.header)

    def test_weverse_missing_date_falls_back(self):
        plan = self.plan(evidence(content_type="WEVERSE_POST", category="weverse", platform="weverse"))
        self.assertFalse(plan.applied)
        self.assertEqual(plan.fallback_reason, "missing_current_evidence:date")

    def test_banila_requires_brand_and_jeonghan(self):
        denied = self.plan(evidence(content_type="BRAND_AD", brand="banila co", has_jeonghan=False))
        allowed = self.plan(evidence(content_type="BRAND_AD", brand="banila co", has_jeonghan=True))
        self.assertFalse(denied.applied)
        self.assertEqual(allowed.rule_id, "banila_co_jeonghan")
        self.assertEqual(allowed.header, "☆ اپدیت برند بانیلاکو با هانی    💒!")

    def test_wrong_brand_is_safe_generic(self):
        plan = self.plan(evidence(content_type="BRAND_AD", brand="dior", has_jeonghan=True))
        self.assertFalse(plan.applied)

    def test_live_event_program_requires_date_and_current_title(self):
        plan = self.plan(evidence(content_type="LIVE_DIALOGUE", category="live", date="260816", title="JEONGHAN LIVE", is_dialogue=True))
        self.assertEqual(plan.rule_id, "dated_live_event_program")
        self.assertTrue(plan.header.startswith("260816 "))
        self.assertTrue(plan.header.endswith(" JEONGHAN LIVE"))

    def test_event_missing_title_falls_back(self):
        plan = self.plan(evidence(content_type="FASHION_EVENT", category="event", date="260816"))
        self.assertFalse(plan.applied)
        self.assertIn("title", plan.fallback_reason)

    def test_non_english_event_title_falls_back(self):
        plan = self.plan(evidence(content_type="FASHION_EVENT", category="event", date="260816", title="رویداد جونگهان"))
        self.assertFalse(plan.applied)

    def test_unknown_category_is_generic(self):
        self.assertFalse(self.plan(evidence()).applied)

    def test_official_news_is_not_assumed_to_be_a_program(self):
        plan = self.plan(evidence(content_type="OFFICIAL_NEWS", category="official", date="260816", title="CURRENT NOTICE"))
        self.assertFalse(plan.applied)

    def test_ambiguous_evidence_is_generic(self):
        plan = self.plan(evidence(platform="instagram", account="jeonghaniyoo_n", ambiguous=True))
        self.assertFalse(plan.applied)
        self.assertEqual(plan.fallback_reason, "ambiguous_current_evidence")

    def test_symbol_selection_is_deterministic(self):
        first = select_rotating_symbol("event-a", self.rules.symbol_pool)
        second = select_rotating_symbol("event-a", self.rules.symbol_pool)
        self.assertEqual(first, second)

    def test_symbol_avoids_immediate_repetition(self):
        first = select_rotating_symbol("event-a", self.rules.symbol_pool)
        second = select_rotating_symbol("event-a", self.rules.symbol_pool, [first])
        self.assertNotEqual(first, second)

    def test_symbol_cold_and_corrupt_state_are_safe(self):
        cold = select_rotating_symbol("cold", self.rules.symbol_pool, None or ())
        corrupt = select_rotating_symbol("cold", self.rules.symbol_pool, [None, 12, "bad"])
        self.assertIn(cold, self.rules.symbol_pool)
        self.assertIn(corrupt, self.rules.symbol_pool)

    def test_body_prefix_skips_url_line(self):
        plan = self.plan(evidence(content_type="WEVERSE_POST", platform="weverse", date="260816"))
        rendered = plan.render("https://example.com/x\nجونگهان امروز پست گذاشت.")
        self.assertIn("https://example.com/x", rendered)
        self.assertRegex(rendered.splitlines()[-1], r"^(?:💒 ⌕|،،⌕໋)")

    def test_dialogue_keeps_speaker_lines_unprefixed(self):
        plan = self.plan(evidence(content_type="LIVE_DIALOGUE", category="live", date="260816", title="JEONGHAN LIVE", is_dialogue=True))
        rendered = plan.render("🪽: امروز خوب بود", is_dialogue=True)
        self.assertTrue(rendered.endswith("🪽: امروز خوب بود"))
        self.assertNotIn("⌕ 🪽:", rendered)

    def test_directive_detects_header_tampering(self):
        plan = self.plan(evidence(content_type="WEVERSE_POST", platform="weverse", date="260816"))
        rendered = plan.render("جونگهان پست گذاشت.").replace("260816", "250101", 1)
        _, failures = plan.factual_projection(rendered)
        self.assertIn("direct_rule_header_changed", failures)

    def test_directive_detects_prefix_tampering(self):
        plan = self.plan(evidence(content_type="WEVERSE_POST", platform="weverse", date="260816"))
        rendered = plan.render("جونگهان پست گذاشت.").replace(plan.body_prefix.strip(), "", 1)
        _, failures = plan.factual_projection(rendered)
        self.assertIn("direct_rule_body_prefix_changed", failures)

    def test_direct_rule_runs_without_historical_corpus(self):
        memory = EmptyMemory()
        try:
            result = rewrite_shadow_candidate(
                memory,
                "جونگهان امروز عکس گذاشت.",
                event_id="evt:ig",
                segment_id="seg:ig",
                content_type="INSTAGRAM_UPDATE",
                direct_evidence=evidence(content_type="INSTAGRAM_UPDATE", platform="instagram", account="jeonghaniyoo_n"),
            )
        finally:
            memory.close()
        self.assertTrue(result.accepted)
        self.assertEqual(result.direct_style_rule_id, "jeonghan_instagram_feed")
        self.assertIn("جونگهان امروز عکس گذاشت.", result.final_text)

    def test_factual_fidelity_overrides_direct_style(self):
        memory = EmptyMemory()
        try:
            result = rewrite_shadow_candidate(
                memory,
                "جونگهان امروز عکس گذاشت.",
                event_id="evt:ig",
                segment_id="seg:ig",
                content_type="INSTAGRAM_UPDATE",
                provider=ReplacingProvider("جاشوآ امروز عکس گذاشت."),
                direct_evidence=evidence(content_type="INSTAGRAM_UPDATE", platform="instagram", account="jeonghaniyoo_n"),
            )
        finally:
            memory.close()
        self.assertFalse(result.accepted)
        self.assertEqual(result.final_text, "جونگهان امروز عکس گذاشت.")

    def test_historical_fact_leakage_cannot_override_direct_rule(self):
        memory = EmptyMemory()
        try:
            result = rewrite_shadow_candidate(
                memory,
                "جونگهان امروز در بوسان بود.",
                event_id="evt:event",
                segment_id="seg:event",
                content_type="FASHION_EVENT",
                provider=ReplacingProvider("جونگهان دیروز در توکیو بود."),
                direct_evidence=evidence(content_type="FASHION_EVENT", category="event", date="260816", title="BANILA CO EVENT", has_jeonghan=True),
            )
        finally:
            memory.close()
        self.assertFalse(result.accepted)
        self.assertNotIn("توکیو", result.final_text)
        self.assertNotIn("دیروز", result.final_text)

    def test_result_metadata_is_body_free_and_shadow_only(self):
        memory = EmptyMemory()
        try:
            result = rewrite_shadow_candidate(
                memory,
                "جونگهان امروز عکس گذاشت.",
                event_id="evt:ig",
                segment_id="seg:ig",
                content_type="INSTAGRAM_UPDATE",
                direct_evidence=evidence(content_type="INSTAGRAM_UPDATE", platform="instagram", account="jeonghaniyoo_n"),
            )
        finally:
            memory.close()
        metadata = result.state_metadata()
        self.assertFalse(metadata["text_persisted"])
        self.assertEqual(metadata["mode"], "shadow")
        self.assertNotIn("جونگهان", repr(metadata))

    def test_symbol_history_is_bounded(self):
        state = _fresh_style_fields()
        state["direct_style_recent_symbols"] = list(self.rules.symbol_pool) * 4
        _prune_style(state)
        self.assertLessEqual(len(state["direct_style_recent_symbols"]), 4)

    def test_rtl_text_has_no_injected_direction_controls(self):
        plan = self.plan(evidence(content_type="INSTAGRAM_UPDATE", platform="instagram", account="jeonghaniyoo_n"))
        rendered = plan.render("جونگهان امروز عکس گذاشت.")
        self.assertNotIn("\u202e", rendered)
        self.assertNotIn("\u202d", rendered)

    def test_plain_text_roundtrip_preserves_forwardable_line_structure(self):
        plan = self.plan(evidence(content_type="FASHION_EVENT", category="event", date="260816", title="CURRENT EVENT"))
        rendered = plan.render("جونگهان در رویداد حاضر شد.")
        copied = rendered.encode("utf-8").decode("utf-8")
        self.assertEqual(copied, rendered)
        self.assertEqual(len(copied.splitlines()), 2)

    def test_urls_numbers_negation_question_and_modality_survive_projection(self):
        factual = "شاید جونگهان ساعت 20:30 نیاد؟ https://example.com/1"
        plan = self.plan(evidence(content_type="WEVERSE_POST", platform="weverse", date="260816"))
        projected, failures = plan.factual_projection(plan.render(factual))
        self.assertFalse(failures)
        self.assertEqual(projected, factual)

    def test_auto_learn_and_style_authority_remain_off(self):
        self.assertFalse(AUTO_LEARN)
        self.assertEqual(STYLE_REWRITE_MODE, "shadow")
        self.assertEqual(DIRECT_STYLE_RULES_MODE, "shadow")

    def test_private_review_and_fanfic_boundaries_remain_independent(self):
        sentry = (ROOT / "app" / "sentry_runtime.py").read_text(encoding="utf-8")
        init = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        fic = (ROOT / "app" / "fic_digest.py").read_text(encoding="utf-8")
        self.assertIn("final_edit_capture_runtime", sentry)
        self.assertNotIn("final_edit_capture", init)
        self.assertNotIn("direct_style_rules", fic)

    def test_configured_sources_private_review_and_free_constraints(self):
        sources = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))["sources"]
        settings = json.loads((ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
        self.assertEqual(len(sources), 24)
        self.assertEqual(sum(bool(item.get("enabled", True)) for item in sources), 23)
        self.assertEqual(
            [item["handle"] for item in sources if not item.get("enabled", True)],
            ["flamehanie"],
        )
        self.assertTrue(settings["runtime"]["review_only"])
        self.assertTrue(all(name not in requirements for name in ("redis", "celery", "pinecone", "qdrant", "weaviate")))

    def test_benchmark_has_at_least_40_cases_and_all_hard_gates_pass(self):
        report = run_direct_style_benchmark()
        self.assertGreaterEqual(report["case_count"], 40)
        self.assertEqual(report["passed"], report["case_count"])
        self.assertEqual(report["failed"], 0)
        self.assertTrue(all(value == 0 for value in report["hard_gates"].values()))


if __name__ == "__main__":
    unittest.main()
