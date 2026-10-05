from __future__ import annotations

"""Clean-room local Ollama adapter for the channel translation-v2 pipeline.

This module intentionally depends only on Ollama's public native HTTP contract.
It does not import or copy OpenClaude implementation code. Ollama is opt-in and
uses the same source-grounded validators/manual-review boundary as Gemini.
"""

import json
import os
import time
from dataclasses import dataclass, field
from types import MethodType
from typing import Any

import requests


DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_NUM_CTX = 32_768
MAX_NUM_CTX = 1_048_576
DEFAULT_TIMEOUT_SECONDS = 60.0


def configured_translation_provider(settings: object) -> str:
    runtime = getattr(settings, "runtime", {}) or {}
    configured = os.getenv("HANI_TRANSLATION_PROVIDER", "").strip()
    if not configured and isinstance(runtime, dict):
        configured = str(runtime.get("translation_provider", "") or "").strip()
    provider = (configured or "gemini").casefold()
    if provider not in {"gemini", "ollama"}:
        raise ValueError("HANI_TRANSLATION_PROVIDER must be 'gemini' or 'ollama'")
    return provider


def _runtime_value(settings: object, env_name: str, runtime_name: str, default: Any) -> Any:
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    runtime = getattr(settings, "runtime", {}) or {}
    if isinstance(runtime, dict):
        value = runtime.get(runtime_name)
        if value not in (None, ""):
            return value
    return default


@dataclass
class OllamaStructuredClient:
    model: str
    base_url: str = DEFAULT_BASE_URL
    num_ctx: int = DEFAULT_NUM_CTX
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    session: Any = field(default_factory=requests.Session)

    def __post_init__(self) -> None:
        self.model = str(self.model or "").strip()
        self.base_url = str(self.base_url or "").strip().rstrip("/")
        if not self.model:
            raise ValueError("OLLAMA_MODEL is required when Ollama translation is enabled")
        if not (
            self.base_url.startswith("http://")
            or self.base_url.startswith("https://")
        ):
            raise ValueError("OLLAMA_BASE_URL must be an http(s) URL")
        try:
            self.num_ctx = int(self.num_ctx)
        except (TypeError, ValueError) as exc:
            raise ValueError("OLLAMA_NUM_CTX must be an integer") from exc
        if not 1 <= self.num_ctx <= MAX_NUM_CTX:
            raise ValueError(f"OLLAMA_NUM_CTX must be in 1..={MAX_NUM_CTX}")
        try:
            self.timeout_seconds = float(self.timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS must be numeric") from exc
        if not 1 <= self.timeout_seconds <= 600:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS must be in 1..=600")

    @classmethod
    def from_settings(cls, settings: object) -> "OllamaStructuredClient":
        model = _runtime_value(settings, "OLLAMA_MODEL", "ollama_model", "")
        base_url = _runtime_value(
            settings,
            "OLLAMA_BASE_URL",
            "ollama_base_url",
            DEFAULT_BASE_URL,
        )
        num_ctx = _runtime_value(
            settings,
            "OLLAMA_NUM_CTX",
            "ollama_num_ctx",
            DEFAULT_NUM_CTX,
        )
        timeout_seconds = _runtime_value(
            settings,
            "OLLAMA_TIMEOUT_SECONDS",
            "ollama_timeout_seconds",
            DEFAULT_TIMEOUT_SECONDS,
        )
        return cls(
            model=str(model),
            base_url=str(base_url),
            num_ctx=int(num_ctx),
            timeout_seconds=float(timeout_seconds),
        )

    def endpoint(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def healthcheck(self) -> str:
        """Verify the local daemon and exact configured model without sending content."""
        try:
            response = self.session.get(
                self.endpoint("/api/tags"),
                timeout=min(10.0, self.timeout_seconds),
            )
        except requests.RequestException as exc:
            return f"fallback (ollama unreachable: {type(exc).__name__})"
        if int(getattr(response, "status_code", 0) or 0) != 200:
            return f"fallback (ollama HTTP {getattr(response, 'status_code', 0)})"
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return "fallback (ollama invalid model-list response)"
        names = {
            str(item.get("name") or item.get("model") or "").strip()
            for item in payload.get("models", [])
            if isinstance(item, dict)
        } if isinstance(payload, dict) else set()
        if self.model not in names:
            return f"fallback (ollama model not installed: {self.model})"
        return f"ok (ollama:{self.model}; ctx={self.num_ctx})"


def _record_failure(writer: object, *, purpose: str, reason: str) -> None:
    diagnostics = getattr(writer, "last_diagnostics", None)
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        setattr(writer, "last_diagnostics", diagnostics)
    failures = diagnostics.setdefault("generation_failures", [])
    if not isinstance(failures, list):
        failures = []
        diagnostics["generation_failures"] = failures
    failures.append(
        {
            "provider": "ollama",
            "model": str(getattr(writer, "_translation_model", ""))[:120],
            "purpose": str(purpose)[:120],
            "reason": str(reason)[:160],
        }
    )
    del failures[:-6]


def generate_json_v2(
    self,
    client: OllamaStructuredClient,
    prompt: str,
    schema: dict[str, Any],
    *,
    temperature: float,
    purpose: str,
    system_instruction: str,
) -> dict[str, Any] | None:
    """Generate one schema-constrained object with Ollama's native chat API."""
    if not isinstance(client, OllamaStructuredClient):
        _record_failure(self, purpose=purpose, reason="invalid_ollama_client")
        return None

    payload = {
        "model": client.model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "format": schema,
        "options": {
            "temperature": float(temperature),
            "num_ctx": client.num_ctx,
        },
    }

    response = None
    for attempt in range(2):
        try:
            response = client.session.post(
                client.endpoint("/api/chat"),
                json=payload,
                timeout=client.timeout_seconds,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == 0:
                time.sleep(0.25)
                continue
            _record_failure(
                self,
                purpose=purpose,
                reason=f"transport:{type(exc).__name__}",
            )
            return None
        except requests.RequestException as exc:
            _record_failure(
                self,
                purpose=purpose,
                reason=f"request:{type(exc).__name__}",
            )
            return None

        status = int(getattr(response, "status_code", 0) or 0)
        if status in {429, 500, 502, 503, 504} and attempt == 0:
            time.sleep(0.25)
            continue
        if not 200 <= status < 300:
            _record_failure(self, purpose=purpose, reason=f"http:{status}")
            return None
        break

    if response is None:
        _record_failure(self, purpose=purpose, reason="no_response")
        return None

    try:
        envelope = response.json()
    except (TypeError, ValueError):
        _record_failure(self, purpose=purpose, reason="invalid_envelope_json")
        return None
    if not isinstance(envelope, dict):
        _record_failure(self, purpose=purpose, reason="envelope_not_object")
        return None

    message = envelope.get("message")
    raw = message.get("content") if isinstance(message, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        _record_failure(self, purpose=purpose, reason="empty_message_content")
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _record_failure(self, purpose=purpose, reason="invalid_structured_json")
        return None
    if not isinstance(parsed, dict) or not parsed:
        _record_failure(self, purpose=purpose, reason="structured_json_not_object")
        return None

    diagnostics = getattr(self, "last_diagnostics", None)
    if isinstance(diagnostics, dict):
        diagnostics["generation_provider"] = "ollama"
        diagnostics["generation_model"] = client.model
        diagnostics["structured_response_source"] = "ollama.message.content"
        diagnostics["local_context_window"] = client.num_ctx
    return parsed


def install_ollama_translation(writer: object, settings: object) -> object:
    """Bind Ollama only to translation-v2 generation, not search/title helpers."""
    if configured_translation_provider(settings) != "ollama":
        setattr(writer, "_translation_provider_name", "gemini")
        setattr(writer, "_translation_model", str(getattr(settings, "gemini_model", "") or ""))
        return writer

    client = OllamaStructuredClient.from_settings(settings)

    def translation_client_or_none(self):
        return self._ollama_translation_client

    setattr(writer, "_ollama_translation_client", client)
    setattr(
        writer,
        "_translation_client_or_none",
        MethodType(translation_client_or_none, writer),
    )
    setattr(writer, "_generate_json_v2", MethodType(generate_json_v2, writer))
    setattr(writer, "_translation_provider_name", "ollama")
    setattr(writer, "_translation_model", client.model)
    return writer
