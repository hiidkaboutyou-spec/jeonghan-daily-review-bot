from __future__ import annotations

"""Live, reproducible writing-quality evaluation for the two production writers.

The translation experiment compares the exact direct-v2 writer with its voice
profile disabled/enabled over 50 real historical non-Persian channel posts.  The
fic experiment fetches a fixed cohort of 20 public AO3 metadata records and judges
only summaries derived from that metadata; story/chapter text is never fetched or
stored.  Fic source blurbs are sent to the evaluator in memory but are deliberately
excluded from the artifact to avoid reproducing copyrighted text.
"""

import argparse
import hashlib
import json
import os
import statistics
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from app.channel_entities import entity_failures
from app.channel_style_runtime import analyze_source, verify_hard_facts
from app.channel_translation import ChannelStyleCaptionWriter
from app.channel_translation_v2_install import install_direct_v2
from app.config import ROOT, Settings
from app.fic_digest import Fic, fetch_ao3_work, summarize_fics_persian
from app.gemini_structured import translation_safety_settings
from app.models import EventGroup, Update
from app.style import StyleMemory
from app.translation_safety import natural_persian_failures, semantic_quality_failures

TRANSLATION_CASES = 50
TRANSLATION_GROUP_SIZE = 5
JUDGE_BATCH_SIZE = 5
FIC_WORKS_PATH = ROOT / "data/fic_quality_evaluation_works.json"


class RequestPacer:
    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, float(interval))
        self.next_at = 0.0

    def wait(self) -> None:
        delay = self.next_at - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.next_at = time.monotonic() + self.interval


def _json_response(response: object) -> dict[str, Any]:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    try:
        value = json.loads(str(getattr(response, "text", "") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class Judge:
    def __init__(self, api_key: str, model: str, pace_seconds: float) -> None:
        from google import genai
        from google.genai import types

        self.types = types
        self.model = model
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=180_000),
        )
        self.pacer = RequestPacer(pace_seconds)

    def generate(self, prompt: str, schema: dict[str, Any], purpose: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(2):
            self.pacer.wait()
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=self.types.GenerateContentConfig(
                        system_instruction=(
                            "You are a strict bilingual Persian editorial evaluator. Judge only "
                            "against the supplied source and metadata. Do not reward extra flourish, "
                            "do not infer missing plot facts, and do not prefer an answer because it "
                            "is labelled A or B. Return only schema-valid JSON."
                        ),
                        response_mime_type="application/json",
                        response_json_schema=schema,
                        thinking_config=self.types.ThinkingConfig(thinking_level="minimal"),
                        safety_settings=translation_safety_settings(self.types),
                    ),
                )
                value = _json_response(response)
                if value:
                    return value
            except Exception as exc:  # provider details are intentionally not persisted
                last_error = exc
                if attempt == 0:
                    time.sleep(4.0)
        raise RuntimeError(f"{purpose} evaluator failed: {type(last_error).__name__}")


def _historical_translation_cases(limit: int = TRANSLATION_CASES) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in sorted((ROOT / "data/channel_style").glob("examples-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                raw = json.loads(line)
                language = str(raw.get("source_language", ""))
                text = str(raw.get("text", "")).strip()
                if language not in {"en", "ko", "ja", "mixed"}:
                    continue
                if not 18 <= len(text) <= 900:
                    continue
                rows.append(
                    {
                        "id": str(raw.get("example_id", "")),
                        "source": text,
                        "source_language": language,
                        "content_type": str(raw.get("content_type", "OTHER")),
                    }
                )
    # Stable stratified selection keeps before/after runs comparable while avoiding
    # a corpus-order or single-content-type bias.
    buckets: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        buckets[(row["source_language"], row["content_type"])].append(row)
    for bucket in buckets.values():
        bucket.sort(key=lambda row: hashlib.sha256(row["id"].encode()).hexdigest())
    selected: list[dict[str, str]] = []
    keys = sorted(buckets)
    while len(selected) < limit and keys:
        next_keys: list[tuple[str, str]] = []
        for key in keys:
            if buckets[key]:
                selected.append(buckets[key].pop(0))
                if len(selected) >= limit:
                    break
            if buckets[key]:
                next_keys.append(key)
        keys = next_keys
    if len(selected) != limit:
        raise RuntimeError(f"historical corpus yielded {len(selected)}/{limit} translation cases")
    return selected


def _translation_group(rows: list[dict[str, str]], group_index: int) -> EventGroup:
    updates = [
        Update(
            id=row["id"],
            url=f"https://example.invalid/history/{row['id']}",
            author="historical_channel_source",
            author_name="historical channel source",
            text=row["source"],
            created_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
            lang=row["source_language"],
        )
        for row in rows
    ]
    return EventGroup(
        key=f"voice-ab:{group_index}",
        category="general",
        title=rows[0]["content_type"],
        updates=updates,
    )


@contextmanager
def _voice_profile(enabled: bool) -> Iterator[None]:
    if enabled:
        yield
        return
    with patch("app.channel_translation_v2._load_voice_profile", return_value=""):
        yield


def _run_translation_arm(
    cases: list[dict[str, str]],
    *,
    api_key: str,
    model: str,
    voice_enabled: bool,
) -> dict[str, dict[str, Any]]:
    memory = StyleMemory(ROOT)
    writer = ChannelStyleCaptionWriter(api_key, model, memory)
    install_direct_v2(writer)
    outputs: dict[str, dict[str, Any]] = {}
    try:
        for index in range(0, len(cases), TRANSLATION_GROUP_SIZE):
            rows = cases[index : index + TRANSLATION_GROUP_SIZE]
            group = _translation_group(rows, index // TRANSLATION_GROUP_SIZE)
            writer.last_diagnostics = {}
            with _voice_profile(voice_enabled):
                copy = writer.write_group(group)
            diagnostics = dict(getattr(writer, "last_diagnostics", {}) or {})
            for row, update in zip(rows, group.updates):
                body = str(copy.bodies.get(update.id, "")).strip()
                analysis = analyze_source(update.translation_source())
                failures = [
                    *verify_hard_facts(update.translation_source(), body, analysis),
                    *entity_failures(update.translation_source(), body),
                    *semantic_quality_failures(update, body),
                    *natural_persian_failures(update, body),
                ]
                outputs[row["id"]] = {
                    "output": body,
                    "deterministic_failures": list(dict.fromkeys(failures)),
                    "output_mode": diagnostics.get("output_mode", ""),
                    "fallback": diagnostics.get("fallback", ""),
                    "voice_profile_loaded": bool(diagnostics.get("voice_profile_loaded")),
                }
    finally:
        memory.close()
    return outputs


_TRANSLATION_SCORE_KEYS = (
    "accuracy",
    "naturalness",
    "colloquial_fluency",
    "emotional_fidelity",
    "human_readability",
)


def _translation_judge_schema() -> dict[str, Any]:
    score_props = {key: {"type": "integer", "minimum": 1, "maximum": 5} for key in _TRANSLATION_SCORE_KEYS}
    return {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "a", "b", "preferred", "issues_a", "issues_b", "reason"],
                    "properties": {
                        "id": {"type": "string"},
                        "a": {"type": "object", "required": list(_TRANSLATION_SCORE_KEYS), "properties": score_props},
                        "b": {"type": "object", "required": list(_TRANSLATION_SCORE_KEYS), "properties": score_props},
                        "preferred": {"type": "string", "enum": ["a", "b", "tie"]},
                        "issues_a": {"type": "array", "items": {"type": "string"}},
                        "issues_b": {"type": "array", "items": {"type": "string"}},
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    }


def evaluate_translation(judge: Judge, api_key: str, model: str) -> dict[str, Any]:
    cases = _historical_translation_cases()
    baseline = _run_translation_arm(cases, api_key=api_key, model=model, voice_enabled=False)
    voice = _run_translation_arm(cases, api_key=api_key, model=model, voice_enabled=True)
    judged: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(cases), JUDGE_BATCH_SIZE):
        payload = []
        mapping: dict[str, bool] = {}
        for case in cases[offset : offset + JUDGE_BATCH_SIZE]:
            case_id = case["id"]
            swap = int(hashlib.sha256(case_id.encode()).hexdigest()[-1], 16) % 2 == 1
            mapping[case_id] = swap
            first = voice[case_id]["output"] if swap else baseline[case_id]["output"]
            second = baseline[case_id]["output"] if swap else voice[case_id]["output"]
            payload.append({"id": case_id, "source": case["source"], "a": first, "b": second})
        prompt = (
            "Blindly score each Persian translation from 1-5 for accuracy, naturalness, "
            "colloquial fluency appropriate to its source, emotional fidelity, and human readability. "
            "Accuracy overrides style. Flag only applicable issues using: hallucination, meaning_drift, "
            "missing_information, overly_formal, unnatural_syntax, repeated_generic_expression, "
            "emoji_overuse. Preserve jokes, teasing, relationship/fandom context, and source emotion.\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        response = judge.generate(prompt, _translation_judge_schema(), "translation A/B")
        for item in response.get("items", []):
            if not isinstance(item, dict) or str(item.get("id")) not in mapping:
                continue
            case_id = str(item["id"])
            swap = mapping[case_id]
            judged[case_id] = {
                "baseline_scores": item.get("b" if swap else "a", {}),
                "voice_scores": item.get("a" if swap else "b", {}),
                "baseline_issues": item.get("issues_b" if swap else "issues_a", []),
                "voice_issues": item.get("issues_a" if swap else "issues_b", []),
                "preferred": (
                    "voice" if item.get("preferred") == ("a" if swap else "b")
                    else "baseline" if item.get("preferred") == ("b" if swap else "a")
                    else "tie"
                ),
                "reason": str(item.get("reason", ""))[:500],
            }
    if len(judged) != len(cases):
        raise RuntimeError(f"translation evaluator returned {len(judged)}/{len(cases)} cases")

    def means(arm: str) -> dict[str, float]:
        return {
            key: round(statistics.mean(float(judged[c["id"]][f"{arm}_scores"][key]) for c in cases), 3)
            for key in _TRANSLATION_SCORE_KEYS
        }

    comparisons = []
    for case in cases:
        case_id = case["id"]
        comparisons.append(
            {
                "id": case_id,
                "source": case["source"],
                "source_language": case["source_language"],
                "content_type": case["content_type"],
                "baseline": baseline[case_id],
                "voice_aware": voice[case_id],
                "evaluation": judged[case_id],
            }
        )
    paired_direct = [
        item for item in comparisons
        if not item["baseline"]["fallback"]
        and not item["voice_aware"]["fallback"]
        and not item["baseline"]["deterministic_failures"]
        and not item["voice_aware"]["deterministic_failures"]
    ]

    def paired_means(arm: str) -> dict[str, float]:
        if not paired_direct:
            return {}
        return {
            key: round(statistics.mean(
                float(item["evaluation"][f"{arm}_scores"][key]) for item in paired_direct
            ), 3)
            for key in _TRANSLATION_SCORE_KEYS
        }

    return {
        "case_count": len(cases),
        "selection": "stable stratified sample of real historical non-Persian channel posts",
        "baseline_means": means("baseline"),
        "voice_aware_means": means("voice"),
        "preference_counts": {
            label: sum(value["preferred"] == label for value in judged.values())
            for label in ("voice", "baseline", "tie")
        },
        "deterministic_failure_counts": {
            "baseline": sum(bool(value["deterministic_failures"]) for value in baseline.values()),
            "voice_aware": sum(bool(value["deterministic_failures"]) for value in voice.values()),
        },
        "paired_direct": {
            "case_count": len(paired_direct),
            "baseline_means": paired_means("baseline"),
            "voice_aware_means": paired_means("voice"),
            "preference_counts": {
                label: sum(item["evaluation"]["preferred"] == label for item in paired_direct)
                for label in ("voice", "baseline", "tie")
            },
        },
        "comparisons": comparisons,
    }


_FIC_SCORE_KEYS = (
    "accuracy",
    "naturalness",
    "completeness",
    "relationship_fidelity",
    "warning_theme_fidelity",
    "spoiler_mode_correctness",
)


def _fic_judge_schema() -> dict[str, Any]:
    score_props = {key: {"type": "integer", "minimum": 1, "maximum": 5} for key in _FIC_SCORE_KEYS}
    return {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["work_id", "scores", "hallucination", "sanitization", "issues"],
                    "properties": {
                        "work_id": {"type": "string"},
                        "scores": {"type": "object", "required": list(_FIC_SCORE_KEYS), "properties": score_props},
                        "hallucination": {"type": "boolean"},
                        "sanitization": {"type": "boolean"},
                        "issues": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }


def _load_real_fics() -> tuple[list[Fic], dict[str, list[str]]]:
    configured = json.loads(FIC_WORKS_PATH.read_text(encoding="utf-8"))
    fics: list[Fic] = []
    coverage: dict[str, list[str]] = {}
    for index, item in enumerate(configured):
        if index:
            time.sleep(1.0)
        work_id = str(item["work_id"])
        fic = None
        for attempt in range(6):
            fic = fetch_ao3_work(f"https://archiveofourown.org/works/{work_id}")
            if fic is not None:
                break
            if attempt < 5:
                # AO3 is volunteer-run and occasionally returns a short burst of
                # 5xx responses to hosted runners. Retry slowly rather than
                # hammering it or invalidating an otherwise completed 50-case A/B.
                time.sleep(min(15.0, 5.0 * (attempt + 1)))
        if fic is None:
            raise RuntimeError(f"AO3 evaluation work unavailable: {work_id}")
        fics.append(fic)
        coverage[work_id] = [str(value) for value in item.get("coverage", [])]
    return fics, coverage


def evaluate_fics(judge: Judge, settings: Settings) -> dict[str, Any]:
    fics, coverage = _load_real_fics()
    medium = summarize_fics_persian(settings, fics)
    judged: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(fics), JUDGE_BATCH_SIZE):
        payload = []
        for fic in fics[offset : offset + JUDGE_BATCH_SIZE]:
            payload.append(
                {
                    "work_id": fic.work_id,
                    "ao3_summary": fic.summary,
                    "relationships": fic.relationships,
                    "rating": fic.rating,
                    "warnings": fic.warnings or [],
                    "tags": fic.freeforms or [],
                    "outputs": {
                        "no_spoiler": fic.summary_fa_nospoiler,
                        "medium_spoiler": medium.get(fic.url, ""),
                        "full": fic.summary_fa_full,
                        "relationship_dynamic": getattr(fic, "relationship_dynamic_fa", ""),
                        "emotional_tone": fic.emotional_tone,
                        "themes": fic.themes or [],
                        "tropes": fic.tropes or [],
                        "why_read": fic.why_read,
                    },
                }
            )
        prompt = (
            "Evaluate summaries derived only from the supplied public AO3 metadata. A full mode may "
            "include every spoiler present in the metadata but must not invent an unseen ending. "
            "No-spoiler must retain premise/atmosphere without later plot details; medium may retain "
            "important conflict but not an ending; full must preserve all supplied details. Mature, "
            "dark, sexual, or violent material must not be censored or softened. Score 1-5 and flag "
            "hallucination, missing_information, wrong_relationship, sanitization, overly_formal, "
            "machine_translation, or spoiler_mode_error.\n" + json.dumps(payload, ensure_ascii=False)
        )
        response = judge.generate(prompt, _fic_judge_schema(), "fic quality")
        for item in response.get("items", []):
            if isinstance(item, dict) and item.get("work_id"):
                judged[str(item["work_id"])] = item
    if len(judged) != len(fics):
        raise RuntimeError(f"fic evaluator returned {len(judged)}/{len(fics)} works")

    # A model judge must never award a structurally absent feature. Apply exact,
    # deterministic coverage failures before aggregating the subjective scores.
    for fic in fics:
        evaluation = judged[fic.work_id]
        scores = evaluation.get("scores", {})
        issues = [str(value) for value in evaluation.get("issues", [])]
        missing_modes = [
            mode for mode, value in (
                ("no_spoiler", fic.summary_fa_nospoiler),
                ("medium_spoiler", medium.get(fic.url, "")),
                ("full", fic.summary_fa_full),
            )
            if not str(value).strip()
        ]
        if missing_modes:
            issues.append("missing_spoiler_modes:" + ",".join(missing_modes))
            scores["completeness"] = 1
            scores["spoiler_mode_correctness"] = 1
        if not fic.relationship_dynamic_fa:
            issues.append("missing_relationship_dynamic")
            scores["relationship_fidelity"] = 1
        if len(fic.warnings_fa or []) < len(fic.warnings or []) or not fic.emotional_tone:
            issues.append("missing_warning_or_tone_metadata")
            scores["warning_theme_fidelity"] = 1
        if not fic.themes and not fic.tropes:
            issues.append("missing_theme_and_trope_metadata")
            scores["warning_theme_fidelity"] = 1
        evaluation["scores"] = scores
        evaluation["issues"] = list(dict.fromkeys(issues))

    means = {
        key: round(statistics.mean(float(judged[fic.work_id]["scores"][key]) for fic in fics), 3)
        for key in _FIC_SCORE_KEYS
    }
    comparisons = []
    for fic in fics:
        comparisons.append(
            {
                "work_id": fic.work_id,
                "url": fic.url,
                "coverage": coverage[fic.work_id],
                "source_sha256": hashlib.sha256(fic.summary.encode("utf-8")).hexdigest(),
                "source_chars": len(fic.summary),
                "relationships": fic.relationships,
                "rating": fic.rating,
                "warnings": fic.warnings or [],
                "tag_count": len(fic.freeforms or []),
                "outputs": {
                    "no_spoiler": fic.summary_fa_nospoiler,
                    "medium_spoiler": medium.get(fic.url, ""),
                    "full": fic.summary_fa_full,
                    "relationship_dynamic": getattr(fic, "relationship_dynamic_fa", ""),
                    "emotional_tone": fic.emotional_tone,
                    "themes": fic.themes or [],
                    "tropes": fic.tropes or [],
                    "why_read": fic.why_read,
                },
                "evaluation": judged[fic.work_id],
            }
        )
    return {
        "case_count": len(fics),
        "source_policy": "public AO3 metadata only; no story/chapter text fetched or stored",
        "score_means": means,
        "hallucination_count": sum(bool(judged[fic.work_id].get("hallucination")) for fic in fics),
        "sanitization_count": sum(bool(judged[fic.work_id].get("sanitization")) for fic in fics),
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--section", choices=("all", "translation", "fic"), default="all")
    parser.add_argument("--pace-seconds", type=float, default=4.5)
    args = parser.parse_args()
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is required for live writing-quality evaluation")
    settings = Settings.load(require_secrets=False)
    model = os.environ.get("GEMINI_MODEL", "").strip() or settings.gemini_model
    judge = Judge(api_key, model, args.pace_seconds)
    result: dict[str, Any] = {
        "evaluation_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": os.environ.get("GITHUB_SHA", ""),
        "model": model,
    }

    def checkpoint() -> None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.section in {"all", "translation"}:
        result["translation"] = evaluate_translation(judge, api_key, model)
        checkpoint()
    if args.section in {"all", "fic"}:
        result["fic"] = evaluate_fics(judge, settings)
        checkpoint()
    print(json.dumps({
        "output": str(args.output),
        "translation": result.get("translation", {}).get("case_count"),
        "fic": result.get("fic", {}).get("case_count"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
