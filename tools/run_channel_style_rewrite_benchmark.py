from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.channel_style_rewrite import (
    MIN_PROFILE_EXAMPLES,
    StyleProfile,
    audit_requested_profiles,
    build_style_rewrite_input,
    evaluate_style_candidate,
    rewrite_shadow_candidate,
    style_fidelity_failures,
)
from app.channel_style_runtime import RetrievedStyleExample
from app.style import StyleMemory

ROOT = Path(__file__).resolve().parents[1]


def _profile(content_type: str, factual: str) -> StyleProfile:
    has_lines = "\n" in factual
    has_dialogue = ":" in factual or "：" in factual
    has_emoji = any(ord(ch) > 0x2600 for ch in factual)
    reaction = any(term in factual for term in ("😂", "😭", "کیوت", "میمیرم", "ㅋㅋ", "ㅎㅎ"))
    return StyleProfile(
        key=content_type,
        content_type=content_type,
        example_count=100,
        register="dialogue" if has_dialogue else ("reaction" if reaction else "factual"),
        median_chars=float(max(1, len(factual))),
        multiline_pct=1.0 if has_lines else 0.0,
        dialogue_pct=1.0 if has_dialogue else 0.0,
        emoji_pct=1.0 if has_emoji else 0.0,
        reaction_pct=1.0 if reaction else 0.0,
        formal_connector_pct=0.0,
        supported=True,
    )


def _example(case_id: str, text: str, content_type: str) -> RetrievedStyleExample:
    return RetrievedStyleExample(
        example_id=f"bench:{case_id}",
        text=text,
        content_type=content_type,
        source_language="fa",
        date="",
        score=1.0,
        reasons=["benchmark structural example"],
    )


def _cases() -> list[dict[str, Any]]:
    long_text = (
        "جونگهان توضیح داد که صبح تمرین داشتن و بعد برای ضبط رفتن. "
        "گفت برنامه طولانی بوده ولی آخرش همه‌چیز خوب پیش رفته و شب برگشتن."
    )
    return [
        {"id":"01_simple_factual","type":"FACTUAL_INFORMATION","f":"جونگهان امروز تمرین داشت.","c":"جونگهان امروز تمرین داشت.","accept":True,"tags":["fidelity"]},
        {"id":"02_casual_live_quote","type":"LIVE_DIALOGUE","f":"🪽: امروز خیلی خوب بود","c":"🪽: امروز خیلی خوب بود","accept":True,"tags":["speaker"]},
        {"id":"03_funny_live_moment","type":"LIVE_DIALOGUE","f":"🪽: این چی بود 😂","c":"🪽: این چی بود 😂","accept":True,"tags":["speaker","style"]},
        {"id":"04_serious_live_moment","type":"WEVERSE_LIVE","f":"🪽: می‌خوام آروم درباره‌ش حرف بزنم.","c":"🪽: می‌خوام آروم درباره‌ش حرف بزنم.","accept":True,"tags":["speaker"]},
        {"id":"05_interview_answer","type":"INTERVIEW","f":"جونگهان گفت این تجربه براش مهم بوده.","c":"جونگهان گفت این تجربه براش مهم بوده.","accept":True,"tags":["name"]},
        {"id":"06_gose_dialogue","type":"LIVE_DIALOGUE","f":"🪽: آماده‌ای؟\n🐶: آره، بریم.","c":"🪽: آماده‌ای؟\n🐶: آره، بریم.","accept":True,"tags":["speaker","question"]},
        {"id":"07_variety_interaction","type":"MEMBER_INTERACTION","f":"جونگهان اول مینگیو رو صدا زد و بعد خندیدن.","c":"جونگهان اول مینگیو رو صدا زد و بعد خندیدن.","accept":True,"tags":["name","chronology"]},
        {"id":"08_reality_content","type":"OTHER","f":"صبح رفتن بیرون و شب برگشتن.","c":"صبح رفتن بیرون و شب برگشتن.","accept":True,"tags":["chronology"]},
        {"id":"09_official_announcement","type":"OFFICIAL_NEWS","f":"برنامه در 18 اوت 2026 منتشر می‌شود.","c":"برنامه در 18 اوت 2026 منتشر می‌شود.","accept":True,"tags":["number_date"]},
        {"id":"10_photo_video_update","type":"VIDEO_REACTION","f":"ویدیوی جدید جونگهان منتشر شد.","c":"ویدیوی جدید جونگهان منتشر شد.","accept":True,"tags":["name"]},
        {"id":"11_fansign_video_call","type":"FANSIGN","f":"فن‌کال: جونگهان گفت ممنونم.","c":"فن‌کال: جونگهان گفت ممنونم.","accept":True,"tags":["name"]},
        {"id":"12_concert_ment","type":"MEMBER_QUOTE","f":"🪽: امشب خیلی خوشحالم.","c":"🪽: امشب خیلی خوشحالم.","accept":True,"tags":["speaker"]},
        {"id":"13_brand_event","type":"BRAND_AD","f":"جونگهان در رویداد برند حاضر شد.","c":"جونگهان در رویداد برند حاضر شد.","accept":True,"tags":["name"]},
        {"id":"14_very_short","type":"SHORT_REACTION","f":"خیلی کیوت 😭","c":"خیلی کیوت 😭","accept":True,"tags":["style"]},
        {"id":"15_long_source","type":"THREAD_OR_LONG_EXPLANATION","f":long_text,"c":long_text,"accept":True,"tags":["chronology"]},
        {"id":"16_multi_speaker","type":"LIVE_DIALOGUE","f":"🪽: سلام\n🐶: خوبی؟\n🪽: آره.","c":"🪽: سلام\n🐶: خوبی؟\n🪽: آره.","accept":True,"tags":["speaker","question"]},
        {"id":"17_quote_context","type":"MEMBER_QUOTE","f":"جونگهان گفت: «امروز خوب بود.»","c":"جونگهان گفت: «امروز خوب بود.»","accept":True,"tags":["name"]},
        {"id":"18_number_date","type":"FACTUAL_INFORMATION","f":"جونگهان گفت 3 نفر در 18 اوت اومدن.","c":"جونگهان گفت 3 نفر در 18 اوت اومدن.","accept":True,"tags":["name","number_date"]},
        {"id":"19_negation","type":"MEMBER_QUOTE","f":"جونگهان گفت نرفت.","c":"جونگهان گفت نرفت.","accept":True,"tags":["name","negation"]},
        {"id":"20_question","type":"MEMBER_QUOTE","f":"جونگهان پرسید: خوبی؟","c":"جونگهان پرسید: خوبی؟","accept":True,"tags":["name","question"]},
        {"id":"21_uncertainty_modality","type":"FACTUAL_INFORMATION","f":"شاید جونگهان بعداً بیاد.","c":"شاید جونگهان بعداً بیاد.","accept":True,"tags":["name","modality"]},
        {"id":"22_slang","type":"SHORT_REACTION","f":"این واقعاً خیلی خوبه.","c":"این واقعاً خیلی خوبه.","accept":True,"tags":["style"]},
        {"id":"23_joke","type":"SHORT_REACTION","f":"این چی بود 😂","c":"این چی بود 😂","accept":True,"tags":["style"]},
        {"id":"24_korean_member_name","type":"MEMBER_QUOTE","f":"정한 گفت امروز خوب بود.","c":"정한 گفت امروز خوب بود.","accept":True,"tags":["name"]},
        {"id":"25_mixed_korean_english","type":"OTHER","f":"정한 گفت comeback خوب پیش رفت.","c":"정한 گفت comeback خوب پیش رفت.","accept":True,"tags":["name"]},
        {"id":"26_historical_fact_leakage","type":"MEMBER_QUOTE","f":"جونگهان گفت رفت بوسان.","c":"جونگهان گفت رفت ژاپن.","accept":False,"examples":["جونگهان گفت رفت ژاپن."],"tags":["leak"]},
        {"id":"27_overly_cute_rewrite","type":"OFFICIAL_NEWS","f":"جونگهان در رویداد حاضر شد.","c":"جونگهان عسلم در رویداد حاضر شد 😭😭😭.","accept":False,"tags":["overstyle"]},
        {"id":"28_overly_formal_rewrite","type":"SHORT_REACTION","f":"این خیلی خوب بود.","c":"لازم به ذکر است این خیلی خوب بود.","accept":False,"tags":["ai_like"]},
        {"id":"29_meaning_preservation","type":"FACTUAL_INFORMATION","f":"جونگهان امروز ساعت 8 رسید.","c":"جونگهان امروز ساعت 8 رسید.","accept":True,"tags":["number_date"]},
        {"id":"30_unsupported_interpretation","type":"MEMBER_INTERACTION","f":"جونگهان به سونگچول لبخند زد.","c":"جونگهان به سونگچول لبخند زد چون عاشقشه.","accept":False,"tags":["interpretation"]},
        {"id":"31_several_style_examples","type":"MEMBER_QUOTE","f":"جونگهان گفت امروز خوب بود.","c":"جونگهان گفت امروز خوب بود.","accept":True,"examples":["هانی گفت خسته‌ست.","جونگهان گفت غذا خورد.","امروز خیلی شلوغ بود."],"tags":["style"]},
        {"id":"32_unrelated_example_rejection","type":"OFFICIAL_NEWS","f":"اعلام شد برنامه فردا منتشر می‌شود.","c":"اعلام شد برنامه فردا منتشر می‌شود.","accept":True,"examples":["دیروز در توکیو کنسرت داشتن."],"tags":["leak"]},
        {"id":"33_example_retrieval_failure","type":"OTHER","f":"جونگهان امروز خوب بود.","special":"retrieval_failure","accept":False,"tags":["fallback"]},
        {"id":"34_style_model_failure","type":"OTHER","f":"جونگهان امروز خوب بود.","special":"provider_failure","accept":False,"tags":["fallback"]},
        {"id":"35_fidelity_lock_rejection","type":"FACTUAL_INFORMATION","f":"3 نفر اومدن.","c":"5 نفر اومدن.","accept":False,"tags":["number_date"]},
        {"id":"36_category_specific_style","type":"OFFICIAL_NEWS","f":"اعلام رسمی منتشر شد.","c":"اعلام رسمی منتشر شد.","accept":True,"tags":["style"]},
        {"id":"37_punctuation_symbol","type":"MEMBER_QUOTE","f":"جونگهان گفت: امروز خوب بود!","c":"جونگهان گفت: امروز خوب بود!","accept":True,"tags":["style"]},
        {"id":"38_dialogue_formatting","type":"LIVE_DIALOGUE","f":"🪽: سلام\n🐶: سلام","c":"🪽: سلام\n🐶: سلام","accept":True,"tags":["speaker"]},
        {"id":"39_ai_like_filler_rejection","type":"INTERVIEW","f":"جونگهان گفت تجربه خوبی بود.","c":"در مجموع جونگهان گفت تجربه خوبی بود.","accept":False,"tags":["ai_like"]},
        {"id":"40_concert_media_independence","type":"MEMBER_QUOTE","f":"🪽: امشب ممنونم.","c":"🪽: امشب ممنونم.","accept":True,"tags":["media_independence"]},
    ]


class _SyntheticMemory:
    def __init__(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE channel_style_examples("
            "example_id TEXT PRIMARY KEY,text TEXT NOT NULL,content_type TEXT NOT NULL,"
            "source_language TEXT NOT NULL,date TEXT NOT NULL,char_count INTEGER NOT NULL,"
            "has_dialogue INTEGER NOT NULL)"
        )
        for index in range(MIN_PROFILE_EXAMPLES):
            text = f"نمونه سبک {index}"
            self.conn.execute(
                "INSERT INTO channel_style_examples VALUES(?,?,?,?,?,?,?)",
                (f"s:{index}", text, "OTHER", "fa", "", len(text), 0),
            )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


class _FailingProvider:
    name = "fixture_failure"

    def rewrite(self, rewrite_input, examples, profile):
        raise RuntimeError("fixture provider unavailable")


def _special_result(case: dict[str, Any]):
    memory = _SyntheticMemory()
    try:
        if case["special"] == "retrieval_failure":
            with patch("app.channel_style_rewrite.retrieve_structural_examples", return_value=[]):
                return rewrite_shadow_candidate(
                    memory, case["f"], event_id="evt:bench", segment_id="seg:bench", content_type="OTHER"
                )
        return rewrite_shadow_candidate(
            memory,
            case["f"],
            event_id="evt:bench",
            segment_id="seg:bench",
            content_type="OTHER",
            provider=_FailingProvider(),
        )
    finally:
        memory.close()


def run(*, include_corpus_audit: bool = False) -> dict[str, Any]:
    rows = []
    tag_totals: dict[str, int] = {}
    tag_passed: dict[str, int] = {}
    style_scores: list[float] = []
    final_fidelity_failures = 0

    for case in _cases():
        if case.get("special"):
            result = _special_result(case)
        else:
            examples = [
                _example(case["id"] + f":{index}", text, case["type"])
                for index, text in enumerate(case.get("examples", []))
            ]
            rewrite_input = build_style_rewrite_input(
                case["f"],
                event_id=f"evt:{case['id']}",
                segment_id=f"seg:{case['id']}",
                content_type=case["type"],
                style_profile=case["type"],
                selected_example_ids=[item.example_id for item in examples],
            )
            result = evaluate_style_candidate(
                rewrite_input,
                case.get("c", ""),
                examples,
                _profile(case["type"], case["f"]),
                provider="benchmark_fixture",
            )

        accepted_expected = bool(case["accept"])
        passed = result.accepted is accepted_expected
        if case["id"] == "33_example_retrieval_failure":
            passed = passed and result.fallback_reason == "style_example_retrieval_failed"
        if case["id"] == "34_style_model_failure":
            passed = passed and result.fallback_reason == "style_provider_failed"
        if case["id"] == "26_historical_fact_leakage":
            passed = passed and any("historical_example_exclusive_token" in item for item in result.fidelity_failures)
        if case["id"] == "40_concert_media_independence":
            passed = passed and not hasattr(
                build_style_rewrite_input(
                    case["f"], event_id="e", segment_id="s", content_type=case["type"]
                ),
                "media",
            )

        final_failures = style_fidelity_failures(case["f"], result.final_text, ())
        if final_failures:
            final_fidelity_failures += 1
            passed = False
        if result.accepted:
            style_scores.append(result.style_score)

        for tag in case.get("tags", []):
            tag_totals[tag] = tag_totals.get(tag, 0) + 1
            if passed:
                tag_passed[tag] = tag_passed.get(tag, 0) + 1

        rows.append({
            "id": case["id"],
            "passed": passed,
            "accepted": result.accepted,
            "fallback_reason": result.fallback_reason,
            "style_score": result.style_score,
        })

    def metric(tag: str) -> float:
        total = tag_totals.get(tag, 0)
        return round(tag_passed.get(tag, 0) / total, 4) if total else 1.0

    profile_audit: dict[str, Any] = {}
    if include_corpus_audit:
        with tempfile.TemporaryDirectory(prefix="style-rewrite-benchmark-") as td:
            memory = StyleMemory(ROOT, db_path=Path(td) / "style.sqlite3")
            try:
                profile_audit = {
                    key: profile.metadata()
                    for key, profile in audit_requested_profiles(memory).items()
                }
            finally:
                memory.close()

    metrics = {
        "final_fidelity_failure_count": final_fidelity_failures,
        "name_accuracy": metric("name"),
        "number_date_accuracy": metric("number_date"),
        "negation_accuracy": metric("negation"),
        "speaker_attribution_accuracy": metric("speaker"),
        "question_accuracy": metric("question"),
        "modality_accuracy": metric("modality"),
        "chronology_accuracy": metric("chronology"),
        "historical_fact_leakage_protection": metric("leak"),
        "unsupported_interpretation_protection": metric("interpretation"),
        "fallback_correctness": metric("fallback"),
        "ai_like_rejection": metric("ai_like"),
        "overstyle_rejection": metric("overstyle"),
        "media_independence": metric("media_independence"),
        "mean_accepted_style_score": round(sum(style_scores) / len(style_scores), 4) if style_scores else 0.0,
    }
    passed = (
        len(rows) == 40
        and all(item["passed"] for item in rows)
        and final_fidelity_failures == 0
        and all(metrics[name] == 1.0 for name in (
            "name_accuracy", "number_date_accuracy", "negation_accuracy",
            "speaker_attribution_accuracy", "question_accuracy", "modality_accuracy",
            "chronology_accuracy", "historical_fact_leakage_protection",
            "unsupported_interpretation_protection", "fallback_correctness",
            "ai_like_rejection", "overstyle_rejection", "media_independence",
        ))
    )
    return {
        "passed": passed,
        "case_count": len(rows),
        **metrics,
        "failed_case_ids": [item["id"] for item in rows if not item["passed"]],
        "profile_audit": profile_audit,
        "cases": rows,
        "note": "Deterministic foundation benchmark only; human-preference quality is not auto-certified.",
    }


def main() -> int:
    result = run(include_corpus_audit=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
