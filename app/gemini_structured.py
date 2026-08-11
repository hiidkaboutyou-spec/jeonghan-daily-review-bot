from __future__ import annotations

"""Small, production-safe adapter for Gemini structured JSON responses.

The Google Gen AI SDK may expose structured output through ``response.parsed``
as well as ``response.text``. Treating an empty text accessor as an API failure
can discard a valid structured response, so production accepts either form while
remaining fail-closed when neither contains a usable JSON object.
"""

import json
import logging
import time
from typing import Any

from . import channel_translation as v1
from .ai import (
    gemini_retryable_provider_failure,
    gemini_shared_failure_kind,
    gemini_should_try_next_model,
)

logger = logging.getLogger(__name__)


def _pace_generation(writer: object) -> None:
    """Keep production request starts below Gemini's project-wide RPM limit.

    The production installer enables this on the one shared writer instance. Unit
    adapters and non-production callers remain unpaced unless they opt in by
    setting ``_gemini_min_request_interval_seconds``.
    """
    try:
        interval = max(0.0, float(getattr(writer, "_gemini_min_request_interval_seconds", 0.0)))
    except (TypeError, ValueError):
        interval = 0.0
    if interval <= 0:
        return

    now = time.monotonic()
    next_request_at = max(0.0, float(getattr(writer, "_gemini_next_request_at", 0.0) or 0.0))
    wait = next_request_at - now
    if wait > 0:
        time.sleep(wait)
        now = time.monotonic()
    # Anchor the next slot to the later value. This keeps request starts spaced
    # even if the monotonic clock is coarse or a previous call finished quickly.
    writer._gemini_next_request_at = max(now, next_request_at) + interval


def _finish_reason(response: object) -> str:
    try:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        value = getattr(candidates[0], "finish_reason", "")
        return str(getattr(value, "name", value) or "")[:80]
    except Exception:
        return ""


def _block_reason(response: object) -> str:
    try:
        feedback = getattr(response, "prompt_feedback", None)
        value = getattr(feedback, "block_reason", "") if feedback is not None else ""
        return str(getattr(value, "name", value) or "")[:80]
    except Exception:
        return ""


def _record_failure(writer: object, *, model: str, purpose: str, reason: str, response: object | None = None) -> None:
    diagnostics = getattr(writer, "last_diagnostics", None)
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        setattr(writer, "last_diagnostics", diagnostics)
    failures = diagnostics.setdefault("generation_failures", [])
    if not isinstance(failures, list):
        failures = []
        diagnostics["generation_failures"] = failures
    entry = {
        "model": str(model)[:120],
        "purpose": str(purpose)[:120],
        "reason": str(reason)[:200],
    }
    if response is not None:
        finish = _finish_reason(response)
        block = _block_reason(response)
        if finish:
            entry["finish_reason"] = finish
        if block:
            entry["block_reason"] = block
    failures.append(entry)
    del failures[:-6]


def _generation_config(types, model: str, *, system_instruction: str, temperature: float, schema: dict[str, Any]):
    """Build the provider-appropriate low-latency structured-output config."""
    common = {
        "system_instruction": system_instruction,
        "response_mime_type": "application/json",
        "response_json_schema": schema,
    }
    if str(model).startswith("gemini-3"):
        # Translation is instruction-following, not deep reasoning. Minimal thinking
        # avoids the ~45s timeout pattern seen in the live EN/KO/JA smoke while our
        # deterministic validators still guard facts, entities and structure.
        common["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
    else:
        # 2.5 Flash/Lite use the older budget API. Keep the fallback path fast.
        common["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        common["temperature"] = temperature
    return types.GenerateContentConfig(**common)


def _read_structured_response(
    writer: object,
    response: object,
    *,
    model: str,
    purpose: str,
) -> dict[str, Any] | None:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict) and parsed:
        if isinstance(getattr(writer, "last_diagnostics", None), dict):
            writer.last_diagnostics["structured_response_source"] = "response.parsed"
            writer.last_diagnostics["generation_model"] = model
        return parsed

    text = str(getattr(response, "text", "") or "").strip()
    if text:
        try:
            parsed_text = json.loads(text)
        except json.JSONDecodeError as exc:
            _record_failure(
                writer,
                model=model,
                purpose=purpose,
                reason=f"invalid_json:{type(exc).__name__}",
                response=response,
            )
        else:
            if isinstance(parsed_text, dict) and parsed_text:
                if isinstance(getattr(writer, "last_diagnostics", None), dict):
                    writer.last_diagnostics["structured_response_source"] = "response.text"
                    writer.last_diagnostics["generation_model"] = model
                return parsed_text
            _record_failure(
                writer,
                model=model,
                purpose=purpose,
                reason="json_not_nonempty_object",
                response=response,
            )
    else:
        _record_failure(
            writer,
            model=model,
            purpose=purpose,
            reason="empty_structured_response",
            response=response,
        )
        logger.warning(
            "Gemini %s model %s returned no usable structured JSON (finish=%s block=%s)",
            purpose,
            model,
            _finish_reason(response) or "none",
            _block_reason(response) or "none",
        )
    return None


def generate_json_v2(
    self,
    client,
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float,
    purpose: str,
    system_instruction: str,
) -> dict[str, Any] | None:
    """Generate one schema-constrained JSON object using the current Gen AI SDK.

    ``schema`` is already a raw JSON Schema dictionary, so use
    ``response_json_schema``. Prefer ``response.parsed`` when available, then fall
    back to parsing ``response.text``. No source/prompt text is copied into
    diagnostics.
    """
    circuit = str(getattr(self, "_gemini_circuit_open", "") or "")
    if circuit:
        diagnostics = getattr(self, "last_diagnostics", None)
        if isinstance(diagnostics, dict):
            diagnostics["generation_circuit_open"] = circuit
        return None

    try:
        from google.genai import types
    except Exception as exc:
        reason = f"types_unavailable:{type(exc).__name__}"
        _record_failure(self, model="", purpose=purpose, reason=reason)
        logger.warning("Gemini types unavailable for %s: %s", purpose, v1._safe_error(exc))
        return None

    all_models = list(self._model_candidates())
    unavailable_models = set(getattr(self, "_gemini_unavailable_models", set()) or set())
    candidates = [model for model in all_models if model not in unavailable_models]
    if not candidates:
        self._gemini_circuit_open = "quota"
        return None

    for model in candidates:
        config = _generation_config(
            types,
            model,
            system_instruction=system_instruction,
            temperature=temperature,
            schema=schema,
        )
        response = None
        final_error: Exception | None = None
        for attempt in range(2):
            try:
                _pace_generation(self)
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=config,
                )
            except Exception as exc:
                if attempt == 0 and gemini_retryable_provider_failure(exc):
                    _record_failure(
                        self,
                        model=model,
                        purpose=purpose,
                        reason=f"transient_retry:{type(exc).__name__}",
                    )
                    logger.warning(
                        "Gemini %s model %s had a temporary provider failure; retrying once",
                        purpose,
                        model,
                    )
                    time.sleep(2.0)
                    continue
                final_error = exc
                break
            else:
                parsed = _read_structured_response(
                    self,
                    response,
                    model=model,
                    purpose=purpose,
                )
                if parsed is not None:
                    return parsed
                break

        if final_error is not None:
            exc = final_error
            shared_failure = gemini_shared_failure_kind(exc)
            model_specific = gemini_should_try_next_model(exc)
            if shared_failure == "quota":
                # Gemini quotas are model-dependent. Retire this model for the
                # current process and try each configured free fallback at most
                # once. If every model is exhausted, open the process circuit so
                # later groups stay queued instead of producing an error storm.
                unavailable_models.add(model)
                self._gemini_unavailable_models = unavailable_models
            elif model_specific:
                # A removed/unknown endpoint will never recover later in this
                # workflow process. Retire it once instead of repeating the same
                # 404 for every queued translation until the job times out.
                unavailable_models.add(model)
                self._gemini_unavailable_models = unavailable_models
            elif shared_failure == "authentication":
                # One workflow process may translate dozens of groups. Once the
                # provider says auth is unavailable, retrying any other model only
                # burns time and floods logs.
                self._gemini_circuit_open = shared_failure
            _record_failure(
                self,
                model=model,
                purpose=purpose,
                reason=f"exception:{type(exc).__name__}:{v1._safe_error(exc)}",
                response=response,
            )
            logger.warning("Gemini %s model %s failed: %s", purpose, model, v1._safe_error(exc))
            if shared_failure == "quota":
                remaining = [candidate for candidate in all_models if candidate not in unavailable_models]
                if remaining:
                    if isinstance(getattr(self, "last_diagnostics", None), dict):
                        self.last_diagnostics["generation_model_failover"] = {
                            "unavailable": sorted(unavailable_models),
                            "next": remaining[0],
                        }
                    continue
                self._gemini_circuit_open = "quota"
            elif model_specific:
                remaining = [candidate for candidate in all_models if candidate not in unavailable_models]
                if remaining:
                    if isinstance(getattr(self, "last_diagnostics", None), dict):
                        self.last_diagnostics["generation_model_failover"] = {
                            "unavailable": sorted(unavailable_models),
                            "next": remaining[0],
                        }
                    continue
                self._gemini_circuit_open = "models_unavailable"
            if not model_specific:
                break
    return None
