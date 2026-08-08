from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypeVar

from app.ai import CaptionWriter
from app.channel_style_runtime import analyze_source, verify_hard_facts
from app.channel_translation import ChannelStyleCaptionWriter
from app.config import ROOT, Settings
from app.models import EventGroup, Update
from app.style import StyleMemory

MIN_STYLED_FRACTION = 0.60
DEFAULT_BATCH_SIZE = 4
DEFAULT_BATCH_COOLDOWN_SECONDS = 65.0
DEFAULT_MAX_QUOTA_RETRIES = 4
_RETRY_RE = re.compile(r"(?:retry(?:ing)?(?: in| after)?|retryDelay[^0-9]*)(?:\s*[:=]?\s*)([0-9]+(?:\.[0-9]+)?)\s*(ms|s)\b", re.I)
_QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota exceeded")
T = TypeVar("T")


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


def _production_model() -> str:
    return Settings.load(require_secrets=False).gemini_model


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:
            self.messages.append(type(record.msg).__name__)


@contextmanager
def _capture_warnings():
    handler = _CaptureHandler()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield handler.messages
    finally:
        root.removeHandler(handler)


def _is_quota_log(messages: list[str]) -> bool:
    joined = "\n".join(messages).lower()
    return any(marker.lower() in joined for marker in _QUOTA_MARKERS)


def _retry_after_seconds(messages: list[str], *, default: float = 60.0) -> float:
    waits: list[float] = []
    for message in messages:
        for value, unit in _RETRY_RE.findall(message):
            seconds = float(value) / 1000.0 if unit.lower() == "ms" else float(value)
            waits.append(seconds)
    return max(waits) if waits else default


def _sanitized_api_diagnostics(messages: list[str], *, attempts: int) -> dict:
    joined = "\n".join(messages)
    quota = _is_quota_log(messages)
    return {
        "attempts": attempts,
        "quota_429": quota,
        "api_status": 429 if quota else None,
        "exception_class": "RESOURCE_EXHAUSTED" if "RESOURCE_EXHAUSTED" in joined else None,
        "retry_after_seconds": round(_retry_after_seconds(messages), 3) if quota else None,
        "neutral_call_quota": quota and "neutral fidelity" in joined.lower(),
        "style_call_quota": quota and "channel style/" in joined.lower(),
        "verifier_call_quota": quota and "fidelity verifier" in joined.lower(),
        "legacy_call_quota": quota and ("caption" in joined.lower() or "legacy" in joined.lower()),
    }


def _run_quota_aware(
    fn: Callable[[], T],
    *,
    retry_if: Callable[[T, list[str]], bool],
    max_quota_retries: int,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[T, dict]:
    all_messages: list[str] = []
    attempts = 0
    while True:
        attempts += 1
        with _capture_warnings() as messages:
            result = fn()
        all_messages.extend(messages)
        quota = _is_quota_log(messages)
        if not quota or not retry_if(result, messages) or attempts > max_quota_retries:
            return result, _sanitized_api_diagnostics(all_messages, attempts=attempts)
        wait = min(max(_retry_after_seconds(messages) + 2.0, 5.0), 75.0)
        print(f"PART4 quota backoff: attempt={attempts}; wait={wait:.1f}s", flush=True)
        sleep_fn(wait)


def _new_needs_quota_retry(writer: ChannelStyleCaptionWriter, messages: list[str]) -> bool:
    fallback = str(writer.last_diagnostics.get("fallback", ""))
    lower = "\n".join(messages).lower()
    all_neutral_failed = lower.count("gemini neutral fidelity model") >= 3
    all_style_failed = lower.count("gemini channel style/") >= 3
    explicit_unavailable = fallback in {
        "gemini_unavailable_neutral",
        "style_transfer_unavailable_neutral",
        "style_transfer_error_neutral",
    }
    return explicit_unavailable or all_neutral_failed or all_style_failed


def _old_needs_quota_retry(_result, messages: list[str]) -> bool:
    lower = "\n".join(messages).lower()
    return "all gemini caption models failed" in lower or lower.count("gemini caption model") >= 3


def _output_mode(fallback: str, api_diag: dict, new_output: str, source: str) -> str:
    if fallback in {
        "gemini_unavailable_neutral",
        "style_transfer_unavailable_neutral",
        "style_transfer_error_neutral",
        "fact_leak_guard_neutral",
        "verifier_error_neutral",
        "post_verifier_fact_leak_neutral",
    }:
        return "neutral_fallback"
    if fallback.startswith("source_") or (
        new_output.strip() == source.strip()
        and api_diag.get("neutral_call_quota")
        and api_diag.get("style_call_quota")
    ):
        return "source_fallback"
    return "styled"


def _summary(results: list[dict]) -> dict:
    styled = sum(item.get("output_mode") == "styled" for item in results)
    neutral = sum(item.get("output_mode") == "neutral_fallback" for item in results)
    source = sum(item.get("output_mode") == "source_fallback" for item in results)
    verifier_passes = sum(item.get("verifier_result") == "PASS" for item in results)
    verifier_failures = len(results) - verifier_passes
    return {
        "styled_successes": styled,
        "neutral_fallbacks": neutral,
        "source_fallbacks": source,
        "fallback_rate": round((neutral + source) / len(results), 4) if results else 1.0,
        "styled_fraction": round(styled / len(results), 4) if results else 0.0,
        "verifier_passes": verifier_passes,
        "verifier_failures": verifier_failures,
    }


def _quality_gate(summary: dict, *, case_count: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    styled = int(summary["styled_successes"])
    styled_fraction = float(summary["styled_fraction"])
    if styled == 0:
        reasons.append("styled_output_success_count_zero")
    if styled_fraction < MIN_STYLED_FRACTION:
        reasons.append(f"styled_fraction_below_{MIN_STYLED_FRACTION:.2f}:{styled_fraction:.4f}")
    if int(summary["verifier_failures"]) > 0:
        reasons.append(f"unresolved_verifier_failures:{summary['verifier_failures']}")
    if case_count < 30 or case_count > 40:
        reasons.append(f"invalid_case_count:{case_count}")
    return not reasons, reasons


def _write_checkpoint(
    output_path: Path,
    *,
    model: str,
    pace_seconds: float,
    batch_size: int,
    batch_cooldown_seconds: float,
    max_quota_retries: int,
    results: list[dict],
    complete: bool,
) -> dict:
    summary = _summary(results)
    passed, reasons = _quality_gate(summary, case_count=len(results)) if complete else (False, ["benchmark_incomplete"])
    payload = {
        "benchmark_version": 3,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "production_model": model,
        "pace_seconds_between_cases": pace_seconds,
        "batch_size": batch_size,
        "batch_cooldown_seconds": batch_cooldown_seconds,
        "max_quota_retries": max_quota_retries,
        "pipeline_old": "app.ai.CaptionWriter.write_group (preserved legacy production path)",
        "pipeline_new": "app.channel_translation.ChannelStyleCaptionWriter.write_group (current production path)",
        "authority_message_count": 16306,
        "recency_weighting": "NONE",
        "date_score_contribution": 0,
        "case_count": len(results),
        "quality_status": "PASS" if passed else ("REJECTED" if complete else "INCOMPLETE"),
        "quality_gate_reasons": reasons,
        "summary": summary,
        "cases": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _load_resume(output_path: Path) -> list[dict]:
    if not output_path.exists():
        return []
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    cases = payload.get("cases", [])
    return list(cases) if isinstance(cases, list) else []


def _resume_case_order(cases: list[dict], prior: list[dict]) -> list[dict]:
    """Rotate unresolved work past the last attempted case without treating it as success."""
    if not prior:
        return list(cases)
    positions = {str(case.get("id")): index for index, case in enumerate(cases)}
    cursor_index = None
    for item in reversed(prior):
        case_id = str(item.get("case_id", ""))
        if case_id in positions:
            cursor_index = positions[case_id]
            break
    if cursor_index is None:
        return list(cases)
    start = (cursor_index + 1) % len(cases)
    return list(cases[start:]) + list(cases[:start])


def run(
    cases_path: Path,
    output_path: Path,
    *,
    pace_seconds: float,
    batch_size: int,
    batch_cooldown_seconds: float,
    max_quota_retries: int,
    resume: bool,
) -> bool:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is required for the real PART 4 benchmark")
    model = _production_model()
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if not 30 <= len(cases) <= 40:
        raise SystemExit(f"expected 30-40 benchmark cases, got {len(cases)}")
    if batch_size < 1:
        raise SystemExit("batch size must be >= 1")
    if max_quota_retries < 0:
        raise SystemExit("max quota retries must be >= 0")

    prior = _load_resume(output_path) if resume else []
    completed_by_id = {
        str(item.get("case_id")): item
        for item in prior
        if item.get("output_mode") == "styled" and item.get("verifier_result") == "PASS"
    }
    ordered_cases = _resume_case_order(cases, prior) if resume else list(cases)
    results: list[dict] = [
        completed_by_id[str(case["id"])]
        for case in cases
        if str(case["id"]) in completed_by_id
    ]
    memory = StyleMemory(ROOT)
    old_writer = CaptionWriter(api_key, model, memory)
    new_writer = ChannelStyleCaptionWriter(api_key, model, memory)
    processed_since_cooldown = 0
    try:
        for index, case in enumerate(ordered_cases):
            case_id = str(case["id"])
            if case_id in completed_by_id:
                continue

            if processed_since_cooldown and processed_since_cooldown >= batch_size:
                print(f"PART4 batch cooldown: completed={processed_since_cooldown}; sleep={batch_cooldown_seconds:.1f}s", flush=True)
                time.sleep(max(0.0, batch_cooldown_seconds))
                processed_since_cooldown = 0
            elif index and pace_seconds > 0:
                time.sleep(pace_seconds)

            group = _group(case)
            source = str(case["source"])
            analysis = analyze_source(source)

            old_copy, old_api = _run_quota_aware(
                lambda: old_writer.write_group(group),
                retry_if=_old_needs_quota_retry,
                max_quota_retries=max_quota_retries,
            )

            if pace_seconds > 0:
                time.sleep(min(max(pace_seconds, 2.0), 8.0))

            def _run_new():
                new_writer.last_diagnostics = {}
                return new_writer.write_group(group)

            new_copy, new_api = _run_quota_aware(
                _run_new,
                retry_if=lambda _result, messages: _new_needs_quota_retry(new_writer, messages),
                max_quota_retries=max_quota_retries,
            )

            old_body = old_copy.bodies.get(case_id, "")
            new_body = new_copy.bodies.get(case_id, "")
            hard_failures = verify_hard_facts(source, new_body, analysis)
            diagnostics = dict(new_writer.last_diagnostics)
            fallback = str(diagnostics.get("fallback", ""))
            mode = _output_mode(fallback, new_api, new_body, source)

            result = {
                "case_id": case["id"],
                "coverage": case.get("coverage", []),
                "source": source,
                "source_language": analysis.source_language,
                "content_type": analysis.content_type,
                "declared_content_type": case.get("content_type", ""),
                "source_analysis": _analysis_payload(analysis),
                "old_legacy_output": old_body,
                "new_channel_style_output": new_body,
                "output_mode": mode,
                "retrieved_historical_example_ids": diagnostics.get("retrieved_example_ids", []),
                "retrieval_scores": diagnostics.get("retrieval_scores", {}),
                "retrieval_reasons": diagnostics.get("retrieval_reasons", {}),
                "glossary_entries_used": diagnostics.get("glossary_entries", []),
                "verifier_result": "PASS" if not hard_failures else "FAIL",
                "verifier_failures": hard_failures,
                "fact_leak_guard_result": "BLOCKED_TO_NEUTRAL" if "fact_leak" in fallback else "PASS_NO_BLOCK",
                "warnings": [fallback] if fallback else [],
                "api_diagnostics": {"old_legacy": old_api, "new_pipeline": new_api},
                "recency_weighting": diagnostics.get("recency_weighting", "NONE"),
                "date_score_contribution": 0,
            }
            results.append(result)
            processed_since_cooldown += 1
            _write_checkpoint(
                output_path,
                model=model,
                pace_seconds=pace_seconds,
                batch_size=batch_size,
                batch_cooldown_seconds=batch_cooldown_seconds,
                max_quota_retries=max_quota_retries,
                results=results,
                complete=False,
            )
            print(
                f"PART4 {case_id}: model={model}; content={analysis.content_type}; mode={mode}; "
                f"fallback={fallback or 'none'}; verifier={'PASS' if not hard_failures else 'FAIL'}; "
                f"new429={new_api['quota_429']}",
                flush=True,
            )
    finally:
        memory.close()

    payload = _write_checkpoint(
        output_path,
        model=model,
        pace_seconds=pace_seconds,
        batch_size=batch_size,
        batch_cooldown_seconds=batch_cooldown_seconds,
        max_quota_retries=max_quota_retries,
        results=results,
        complete=True,
    )
    summary = payload["summary"]
    if payload["quality_status"] != "PASS":
        print(
            "PART4 BENCHMARK REJECTED: "
            f"styled={summary['styled_successes']}/{len(results)}; neutral={summary['neutral_fallbacks']}; "
            f"source={summary['source_fallbacks']}; verifier_failures={summary['verifier_failures']}; "
            f"reasons={','.join(payload['quality_gate_reasons'])}",
            flush=True,
        )
        return False

    print(
        f"PART4 BENCHMARK QUALITY PASS: {len(results)} cases; styled={summary['styled_successes']}; "
        f"fallback_rate={summary['fallback_rate']:.2%} -> {output_path}",
        flush=True,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "translation_benchmark_cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pace-seconds", type=float, default=5.0)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--batch-cooldown-seconds", type=float, default=DEFAULT_BATCH_COOLDOWN_SECONDS)
    parser.add_argument("--max-quota-retries", type=int, default=DEFAULT_MAX_QUOTA_RETRIES)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    passed = run(
        args.cases,
        args.output,
        pace_seconds=args.pace_seconds,
        batch_size=args.batch_size,
        batch_cooldown_seconds=args.batch_cooldown_seconds,
        max_quota_retries=args.max_quota_retries,
        resume=args.resume,
    )
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())