"""Deterministic benchmark for evidence-bound Direct User Style Rules."""
from __future__ import annotations

import json
from typing import Any

from app.channel_style_rewrite import style_fidelity_failures
from app.direct_style_rules import DirectStyleEvidence, DirectStylePlanner, DirectStyleRuleSet


def _e(**values: Any) -> DirectStyleEvidence:
    defaults = {
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
    defaults.update(values)
    return DirectStyleEvidence(**defaults)


def _cases() -> list[dict[str, Any]]:
    return [
        {"id":"01_ig_feed","f":"جونگهان امروز عکس گذاشت.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n"),"rule":"jeonghan_instagram_feed"},
        {"id":"02_ig_number","f":"جونگهان 3 تا عکس گذاشت.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n"),"rule":"jeonghan_instagram_feed"},
        {"id":"03_ig_url","f":"عکس‌های جونگهان منتشر شد.\nhttps://instagram.com/p/current", "e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n"),"rule":"jeonghan_instagram_feed"},
        {"id":"04_ig_negation","f":"جونگهان امروز پست جدیدی نگذاشت.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n"),"rule":"jeonghan_instagram_feed"},
        {"id":"05_ig_question","f":"این عکس جدید جونگهان بود؟","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n"),"rule":"jeonghan_instagram_feed"},
        {"id":"06_story","f":"جونگهان یک استوری گذاشت.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n",is_story=True),"rule":"jeonghan_instagram_story"},
        {"id":"07_story_url","f":"https://instagram.com/stories/current\nاستوری جدید جونگهان","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n",is_story=True),"rule":"jeonghan_instagram_story"},
        {"id":"08_story_other_account","f":"یک استوری جدید منتشر شد.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="other",is_story=True),"rule":""},
        {"id":"09_story_ambiguous","f":"استوری جدید جونگهان","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n",is_story=True,ambiguous=True),"rule":""},
        {"id":"10_feed_not_story","f":"پست فید جونگهان منتشر شد.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n",is_story=False),"rule":"jeonghan_instagram_feed"},
        {"id":"11_weverse","f":"جونگهان در ویورس پست گذاشت.","e":_e(content_type="WEVERSE_POST",platform="weverse",date="260816"),"rule":"weverse_update"},
        {"id":"12_weverse_number","f":"جونگهان ساعت 20:30 پست گذاشت.","e":_e(content_type="WEVERSE_POST",platform="weverse",date="260816"),"rule":"weverse_update"},
        {"id":"13_weverse_missing_date","f":"جونگهان در ویورس پست گذاشت.","e":_e(content_type="WEVERSE_POST",platform="weverse"),"rule":"weverse_update","applied":False},
        {"id":"14_weverse_bad_date","f":"جونگهان در ویورس پست گذاشت.","e":_e(content_type="WEVERSE_POST",platform="weverse",date="2026-08-16"),"rule":"weverse_update","applied":False},
        {"id":"15_weverse_question","f":"جونگهان پرسید: خوبی؟","e":_e(content_type="WEVERSE_POST",platform="weverse",date="260816"),"rule":"weverse_update"},
        {"id":"16_banila","f":"جونگهان در کمپین جدید بانیلاکو حضور داشت.","e":_e(content_type="BRAND_AD",brand="banila co",has_jeonghan=True),"rule":"banila_co_jeonghan"},
        {"id":"17_banila_number","f":"3 عکس جدید از جونگهان منتشر شد.","e":_e(content_type="BRAND_AD",brand="banila co",has_jeonghan=True),"rule":"banila_co_jeonghan"},
        {"id":"18_banila_wrong_brand","f":"جونگهان در کمپین دیور حضور داشت.","e":_e(content_type="BRAND_AD",brand="dior",has_jeonghan=True),"rule":""},
        {"id":"19_banila_no_jeonghan","f":"کمپین جدید بانیلاکو منتشر شد.","e":_e(content_type="BRAND_AD",brand="banila co",has_jeonghan=False),"rule":""},
        {"id":"20_brand_ambiguous","f":"آپدیت برند منتشر شد.","e":_e(content_type="BRAND_AD",brand="banila co",has_jeonghan=True,ambiguous=True),"rule":""},
        {"id":"21_live","f":"🪽: امروز خیلی خوب بود","e":_e(content_type="LIVE_DIALOGUE",category="live",date="260816",title="JEONGHAN LIVE",is_dialogue=True),"rule":"dated_live_event_program"},
        {"id":"22_live_speakers","f":"🪽: آماده‌ای؟\n🐶: آره، بریم.","e":_e(content_type="LIVE_DIALOGUE",category="live",date="260816",title="JEONGHAN LIVE",is_dialogue=True),"rule":"dated_live_event_program"},
        {"id":"23_event","f":"جونگهان در رویداد حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="BANILA CO EVENT"),"rule":"dated_live_event_program"},
        {"id":"24_program","f":"قسمت جدید برنامه منتشر شد.","e":_e(content_type="OFFICIAL_NEWS",category="program",date="260816",title="NANA TOUR"),"rule":"dated_live_event_program"},
        {"id":"25_event_missing_date","f":"جونگهان در رویداد حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",title="BANILA CO EVENT"),"rule":"dated_live_event_program","applied":False},
        {"id":"26_event_missing_title","f":"جونگهان در رویداد حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",date="260816"),"rule":"dated_live_event_program","applied":False},
        {"id":"27_event_persian_title","f":"جونگهان در رویداد حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="رویداد جونگهان"),"rule":"dated_live_event_program","applied":False},
        {"id":"28_program_url","f":"جزئیات برنامه:\nhttps://example.com/current","e":_e(content_type="OFFICIAL_NEWS",category="program",date="260816",title="JEONGHAN PROGRAM"),"rule":"dated_live_event_program"},
        {"id":"29_event_mixed","f":"정한 در event جدید حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="JEONGHAN EVENT"),"rule":"dated_live_event_program"},
        {"id":"30_live_modality","f":"شاید جونگهان امشب بیاد.","e":_e(content_type="LIVE_DIALOGUE",category="live",date="260816",title="JEONGHAN LIVE"),"rule":"dated_live_event_program"},
        {"id":"31_unknown","f":"جونگهان امروز خوب بود.","e":_e(),"rule":""},
        {"id":"32_ambiguous_platform","f":"آپدیت جدید جونگهان","e":_e(platform="instagram",account="jeonghaniyoo_n",ambiguous=True),"rule":""},
        {"id":"33_instagram_other_account","f":"پست جدید منتشر شد.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="other"),"rule":""},
        {"id":"34_weverse_ambiguous","f":"پست جدید جونگهان","e":_e(content_type="WEVERSE_POST",platform="weverse",date="260816",ambiguous=True),"rule":""},
        {"id":"35_generic_brand","f":"آپدیت برند منتشر شد.","e":_e(content_type="BRAND_AD"),"rule":""},
        {"id":"36_rtl_prefix","f":"جونگهان امروز برگشت.","e":_e(content_type="WEVERSE_POST",platform="weverse",date="260816"),"rule":"weverse_update"},
        {"id":"37_emoji_leading","f":"🩷 جونگهان امروز برگشت.","e":_e(content_type="INSTAGRAM_UPDATE",platform="instagram",account="jeonghaniyoo_n"),"rule":"jeonghan_instagram_feed"},
        {"id":"38_list_safety","f":"- ساعت 20:30\n- لینک رسمی","e":_e(content_type="WEVERSE_POST",platform="weverse",date="260816"),"rule":"weverse_update"},
        {"id":"39_dialogue_no_prefix","f":"🪽: امروز نمیام.","e":_e(content_type="LIVE_DIALOGUE",category="live",date="260816",title="JEONGHAN LIVE",is_dialogue=True),"rule":"dated_live_event_program"},
        {"id":"40_mixed_title_body","f":"جونگهان دربارهٔ NANA TOUR حرف زد.","e":_e(content_type="OFFICIAL_NEWS",category="program",date="260816",title="NANA TOUR"),"rule":"dated_live_event_program"},
        {"id":"41_historical_date_guard","f":"جونگهان امروز در بوسان بود.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="CURRENT EVENT"),"rule":"dated_live_event_program","forbid":["250808"]},
        {"id":"42_historical_title_guard","f":"جونگهان امروز در بوسان بود.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="CURRENT EVENT"),"rule":"dated_live_event_program","forbid":["SEUNGKWAN’S NOONA WEDDING"]},
        {"id":"43_historical_brand_guard","f":"جونگهان امروز در رویداد حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="CURRENT EVENT"),"rule":"dated_live_event_program","forbid":["Dior","Gucci"]},
        {"id":"44_rotation_repeat","f":"جونگهان در رویداد حاضر شد.","e":_e(content_type="FASHION_EVENT",category="event",date="260816",title="CURRENT EVENT"),"rule":"dated_live_event_program","rotation":True},
        {"id":"45_cold_start","f":"جونگهان در برنامه حاضر شد.","e":_e(content_type="OFFICIAL_NEWS",category="program",date="260816",title="CURRENT PROGRAM"),"rule":"dated_live_event_program","cold":True},
        {"id":"46_official_not_program","f":"اطلاعیهٔ رسمی جدید منتشر شد.","e":_e(content_type="OFFICIAL_NEWS",category="official",date="260816",title="CURRENT NOTICE"),"rule":""},
    ]


def run() -> dict[str, Any]:
    rules = DirectStyleRuleSet.load()
    planner = DirectStylePlanner(rules)
    rows: list[dict[str, Any]] = []
    unsupported_additions = 0
    historical_leakage = 0
    category_false_positives = 0
    fidelity_failures = 0
    deterministic_failures = 0

    for case in _cases():
        plan = planner.plan(case["e"], context_key=f"benchmark:{case['id']}")
        second = planner.plan(case["e"], context_key=f"benchmark:{case['id']}")
        rendered = plan.render(case["f"], is_dialogue=case["e"].is_dialogue)
        projected, directive_failures = plan.factual_projection(rendered, is_dialogue=case["e"].is_dialogue)
        fact_failures = style_fidelity_failures(case["f"], projected)
        expected_rule = case["rule"]
        expected_applied = bool(case.get("applied", bool(expected_rule)))
        passed = plan.rule_id == expected_rule and plan.applied is expected_applied
        if plan != second:
            deterministic_failures += 1
            passed = False
        if directive_failures or fact_failures or projected != case["f"]:
            fidelity_failures += 1
            unsupported_additions += len(fact_failures)
            passed = False
        for forbidden in case.get("forbid", []):
            if forbidden.casefold() in rendered.casefold():
                historical_leakage += 1
                passed = False
        if not expected_applied and plan.applied:
            category_false_positives += 1
            passed = False
        if case.get("rotation"):
            rotated = planner.plan(case["e"], context_key=f"benchmark:{case['id']}", recent_symbols=[plan.symbol])
            if not plan.symbol or not rotated.symbol or plan.symbol == rotated.symbol:
                passed = False
                deterministic_failures += 1
        if case.get("cold") and plan.symbol not in rules.symbol_pool:
            passed = False
        rows.append({
            "id": case["id"],
            "passed": passed,
            "rule_id": plan.rule_id,
            "applied": plan.applied,
            "fallback_reason": plan.fallback_reason,
            "symbol": plan.symbol,
        })

    passed_count = sum(bool(item["passed"]) for item in rows)
    report = {
        "benchmark": "direct_user_style_rules_foundation",
        "case_count": len(rows),
        "passed": passed_count,
        "failed": len(rows) - passed_count,
        "hard_gates": {
            "unsupported_factual_additions": unsupported_additions,
            "historical_factual_leakage": historical_leakage,
            "category_false_positives": category_false_positives,
            "fidelity_failures": fidelity_failures,
            "deterministic_failures": deterministic_failures,
        },
        "authority_order": list(rules.authority_order),
        "mode": "shadow",
        "rows": rows,
    }
    return report


def main() -> int:
    report = run()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"DIRECT USER STYLE RULES BENCHMARK: {report['passed']}/{report['case_count']} passed")
    return 0 if report["failed"] == 0 and all(value == 0 for value in report["hard_gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
