from __future__ import annotations

import hashlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable

from app.ai import CaptionWriter, GroupCopy
from app.channel_translation import ChannelStyleCaptionWriter
from app.config import Settings
from tools import run_translation_benchmark as benchmark

CACHE_VERSION = 1
DEFAULT_QUOTA_FAIL_FAST_CASES = 2


def _stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _output_path_from_argv(argv: list[str]) -> Path:
    for index, value in enumerate(argv):
        if value == "--output" and index + 1 < len(argv):
            return Path(argv[index + 1])
        if value.startswith("--output="):
            return Path(value.split("=", 1)[1])
    raise SystemExit("--output is required")


class QuotaFailFast(RuntimeError):
    """Benchmark-only signal that sustained external quota exhaustion was checkpointed."""


class _QuotaFailFastController:
    """Stop only after consecutive checkpointed cases hit Gemini quota.

    This controller never changes production behavior or benchmark quality semantics.
    It observes the already-materialized benchmark result after the normal checkpoint
    has been written. Successful or non-quota cases reset the streak.
    """

    def __init__(self, threshold: int = DEFAULT_QUOTA_FAIL_FAST_CASES):
        if threshold < 1:
            raise ValueError("quota fail-fast threshold must be >= 1")
        self.threshold = threshold
        self.consecutive_quota_cases = 0

    def observe_checkpoint(self, payload: dict[str, Any], *, complete: bool) -> bool:
        if complete:
            self.consecutive_quota_cases = 0
            return False
        cases = payload.get("cases", [])
        if not isinstance(cases, list) or not cases:
            return False
        latest = cases[-1]
        diagnostics = latest.get("api_diagnostics", {}) if isinstance(latest, dict) else {}
        old_diag = diagnostics.get("old_legacy", {}) if isinstance(diagnostics, dict) else {}
        new_diag = diagnostics.get("new_pipeline", {}) if isinstance(diagnostics, dict) else {}
        quota = bool(
            isinstance(old_diag, dict) and old_diag.get("quota_429")
            or isinstance(new_diag, dict) and new_diag.get("quota_429")
        )
        if quota:
            self.consecutive_quota_cases += 1
        else:
            self.consecutive_quota_cases = 0
        return self.consecutive_quota_cases >= self.threshold


class _StageCache:
    """Benchmark-only cache for successful real model stages.

    It stores only parsed model outputs keyed by a hash of the exact prompt/config,
    plus successful legacy GroupCopy results. It never stores API keys or prompts.
    """

    def __init__(self, path: Path, production_model: str):
        self.path = path
        self.production_model = production_model
        self.responses: dict[str, dict[str, Any]] = {}
        self.legacy: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        cached = payload.get("api_stage_cache", {})
        if not isinstance(cached, dict):
            return
        if cached.get("version") != CACHE_VERSION:
            return
        if str(cached.get("production_model", "")) != self.production_model:
            return
        responses = cached.get("responses", {})
        legacy = cached.get("legacy", {})
        if isinstance(responses, dict):
            self.responses = {str(k): v for k, v in responses.items() if isinstance(v, dict)}
        if isinstance(legacy, dict):
            self.legacy = {str(k): v for k, v in legacy.items() if isinstance(v, dict)}

    def persist(self) -> None:
        payload: dict[str, Any] = {}
        if self.path.exists():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(current, dict):
                    payload = current
            except (OSError, json.JSONDecodeError):
                payload = {}
        payload["api_stage_cache"] = {
            "version": CACHE_VERSION,
            "production_model": self.production_model,
            "responses": self.responses,
            "legacy": self.legacy,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def response_key(self, *, writer_model: str, purpose: str, prompt: str, schema: dict[str, Any], temperature: float) -> str:
        return _stable_hash(
            {
                "kind": "channel-stage",
                "writer_model": writer_model,
                "purpose": purpose,
                "prompt": prompt,
                "schema": schema,
                "temperature": temperature,
            }
        )

    def get_response(self, key: str) -> dict[str, Any] | None:
        value = self.responses.get(key)
        return json.loads(json.dumps(value, ensure_ascii=False)) if isinstance(value, dict) else None

    def put_response(self, key: str, parsed: dict[str, Any]) -> None:
        self.responses[key] = json.loads(json.dumps(parsed, ensure_ascii=False))
        self.persist()

    def legacy_key(self, writer: CaptionWriter, group, code_fingerprint: str) -> str:
        return _stable_hash(
            {
                "kind": "legacy-group",
                "writer_model": writer.model,
                "code": code_fingerprint,
                "category": group.category,
                "title": group.title,
                "updates": [
                    {
                        "id": item.id,
                        "url": item.url,
                        "author": item.author,
                        "text": item.text,
                        "lang": item.lang,
                    }
                    for item in group.updates
                ],
                "style_profile": writer.memory.profile,
            }
        )

    def get_legacy(self, key: str) -> GroupCopy | None:
        value = self.legacy.get(key)
        if not isinstance(value, dict):
            return None
        bodies = value.get("bodies")
        if not isinstance(bodies, dict):
            return None
        return GroupCopy(
            title=str(value.get("title", "")),
            category=str(value.get("category", "general")),
            bodies={str(k): str(v) for k, v in bodies.items()},
        )

    def put_legacy(self, key: str, copy: GroupCopy) -> None:
        self.legacy[key] = {
            "title": copy.title,
            "category": copy.category,
            "bodies": dict(copy.bodies),
        }
        self.persist()


def _install_stage_cache(
    cache: _StageCache,
    quota_controller: _QuotaFailFastController | None = None,
) -> Callable[[], None]:
    original_generate = ChannelStyleCaptionWriter._generate_json
    original_legacy_write = CaptionWriter.write_group
    original_checkpoint = benchmark._write_checkpoint
    legacy_code_fingerprint = hashlib.sha256(inspect.getsource(original_legacy_write).encode("utf-8")).hexdigest()
    quota_controller = quota_controller or _QuotaFailFastController()

    def cached_generate(self, client, prompt, schema, *, temperature, purpose):
        key = cache.response_key(
            writer_model=self.model,
            purpose=purpose,
            prompt=prompt,
            schema=schema,
            temperature=temperature,
        )
        cached = cache.get_response(key)
        if cached is not None:
            print(f"PART4 stage cache hit: {purpose}", flush=True)
            return cached
        parsed = original_generate(
            self,
            client,
            prompt,
            schema,
            temperature=temperature,
            purpose=purpose,
        )
        if isinstance(parsed, dict) and parsed:
            cache.put_response(key, parsed)
            print(f"PART4 stage cache saved: {purpose}", flush=True)
        return parsed

    def cached_legacy_write(self, group, *, mode="default"):
        key = cache.legacy_key(self, group, legacy_code_fingerprint)
        cached = cache.get_legacy(key)
        if cached is not None:
            print(f"PART4 legacy cache hit: {group.key}", flush=True)
            return cached
        result = original_legacy_write(self, group, mode=mode)
        fallback = self._fallback_group(group)
        if result.bodies != fallback.bodies or result.title != fallback.title:
            cache.put_legacy(key, result)
            print(f"PART4 legacy cache saved: {group.key}", flush=True)
        return result

    def cached_checkpoint(*args, **kwargs):
        payload = original_checkpoint(*args, **kwargs)
        cache.persist()
        complete = bool(kwargs.get("complete", False))
        if quota_controller.observe_checkpoint(payload, complete=complete):
            print(
                "PART4 quota fail-fast: sustained 429/RESOURCE_EXHAUSTED after "
                f"{quota_controller.consecutive_quota_cases} checkpointed cases; exiting with progress preserved.",
                flush=True,
            )
            raise QuotaFailFast("sustained Gemini quota exhaustion; checkpoint preserved")
        return payload

    ChannelStyleCaptionWriter._generate_json = cached_generate
    CaptionWriter.write_group = cached_legacy_write
    benchmark._write_checkpoint = cached_checkpoint

    def restore() -> None:
        ChannelStyleCaptionWriter._generate_json = original_generate
        CaptionWriter.write_group = original_legacy_write
        benchmark._write_checkpoint = original_checkpoint

    return restore


def main() -> int:
    output_path = _output_path_from_argv(sys.argv[1:])
    model = Settings.load(require_secrets=False).gemini_model
    cache = _StageCache(output_path, model)
    restore = _install_stage_cache(cache)
    try:
        return benchmark.main()
    except QuotaFailFast:
        return 3
    finally:
        cache.persist()
        restore()


if __name__ == "__main__":
    raise SystemExit(main())
