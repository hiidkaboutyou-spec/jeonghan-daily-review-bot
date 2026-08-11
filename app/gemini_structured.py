from __future__ import annotations

"""Small, production-safe adapter for Gemini structured JSON responses.

The Google Gen AI SDK may expose structured output through ``response.parsed``
as well as ``response.text``. Treating an empty text accessor as an API failure
can discard a valid structured response, so production accepts either form while
remaining fail-closed when neither contains a usable JSON object.
"""

import json
import logging
from typing import Any

from . import channel_translation as v1
from .ai import gemini_should_try_next_model

logger = logging.getLogger(__name__)


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
    ``response_json_schema``. ``response_schema`` is the higher-level schema/type
    input and is not the right contract for this raw dictionary path. Prefer
    ``response.parsed`` when available, then fall back to parsing ``response.text``.
    No source/prompt text is copied into diagnostics.
    """
    try:
        from google.genai import types
    except Exception as exc:
        reason = f"types_unavailable:{type(exc).__name__}"
        _record_failure(self, model="", purpose=purpose, reason=reason)
        logger.warning("Gemini types unavailable for %s: %s", purpose, v1._safe_error(exc))
        return None

    for model in self._model_candidates():
        response = None
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
                response_mime_type="application/json",
                response_json_schema=schema,
            )
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )

            parsed = getattr(response, "parsed", None)
            if isinstance(parsed, dict) and parsed:
                if isinstance(getattr(self, "last_diagnostics", None), dict):
                    self.last_diagnostics["structured_response_source"] = "response.parsed"
                return parsed

            text = str(getattr(response, "text", "") or "").strip()
            if text:
                try:
                    parsed_text = json.loads(text)
                except json.JSONDecodeError as exc:
                    _record_failure(
                        self,
                        model=model,
                        purpose=purpose,
                        reason=f"invalid_json:{type(exc).__name__}",
                        response=response,
                    )
                else:
                    if isinstance(parsed_text, dict) and parsed_text:
                        if isinstance(getattr(self, "last_diagnostics", None), dict):
                            self.last_diagnostics["structured_response_source"] = "response.text"
                        return parsed_text
                    _record_failure(
                        self,
                        model=model,
                        purpose=purpose,
                        reason="json_not_nonempty_object",
                        response=response,
                    )
            else:
                _record_failure(
                    self,
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
        except Exception as exc:
            _record_failure(
                self,
                model=model,
                purpose=purpose,
                reason=f"exception:{type(exc).__name__}:{v1._safe_error(exc)}",
                response=response,
            )
            logger.warning("Gemini %s model %s failed: %s", purpose, model, v1._safe_error(exc))
            if not gemini_should_try_next_model(exc):
                break
    return None
