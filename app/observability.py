from __future__ import annotations

import json
import logging
import os
import re
import uuid
from contextvars import ContextVar, Token
from typing import Any

_INITIALIZED = False
logger = logging.getLogger(__name__)
_RETRIEVAL_ATTEMPT: ContextVar[str] = ContextVar("retrieval_attempt_id", default="")

# Sentry and structured logs receive metadata only. No free-form source text,
# captions, request bodies, URLs, headers, cookies or environment values belong here.
_ALLOWED_TAGS = {
    "event",
    "component",
    "stage",
    "status",
    "update_id",
    "source",
    "post_id",
    "event_id",
    "retrieval_attempt_id",
    "media_asset_id",
    "translation_job_id",
    "delivery_key",
    "delivery_receipt_id",
    "error_class",
    "retry_count",
    "provider",
    "model",
    "fallback",
    "complete",
    "partial",
    "cutoff_crossed",
    "provider_exhausted",
    "include_replies",
    "raw_seen",
    "discovered",
    "retained",
    "external_dropped",
    "duplicate_dropped",
    "media_count",
    "prepared_count",
    "retrieval_method",
    "cursor_advanced",
    "cursor_reason",
    "pagination",
    "pages_requested",
    "cursor_requested",
}
_SECRET_HINT_RE = re.compile(
    r"(?:bearer\s+|authorization|telegram[_-]?bot[_-]?token|x[_-]?cookie|gemini[_-]?api[_-]?key|"
    r"api[_-]?key|password|credential|session[_-]?token|auth[_-]?token|cookie\s*=)",
    re.I,
)


def _safe_metadata_value(value: Any, *, limit: int = 160) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip().replace("\n", " ").replace("\r", " ")[:limit]
    if _SECRET_HINT_RE.search(text):
        return "<redacted>"
    return text


def safe_metadata(metadata: dict[str, Any]) -> dict[str, str]:
    """Return only bounded, non-content correlation metadata."""
    safe: dict[str, str] = {}
    for key, value in metadata.items():
        key = str(key)
        if key not in _ALLOWED_TAGS:
            continue
        safe[key] = _safe_metadata_value(value)
    return safe


def new_attempt_id() -> str:
    """Create an explicitly attempt-level ID; stable update/post IDs remain unchanged."""
    return uuid.uuid4().hex[:20]


def current_retrieval_attempt_id() -> str:
    return _RETRIEVAL_ATTEMPT.get()


def set_retrieval_attempt(attempt_id: str) -> Token:
    return _RETRIEVAL_ATTEMPT.set(_safe_metadata_value(attempt_id, limit=40))


def reset_retrieval_attempt(token: Token) -> None:
    _RETRIEVAL_ATTEMPT.reset(token)


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Return a minimal technical Sentry event with private review data removed."""
    safe: dict[str, Any] = {
        "level": event.get("level", "error"),
        "platform": event.get("platform", "python"),
    }
    if event.get("timestamp") is not None:
        safe["timestamp"] = event["timestamp"]
    if event.get("release"):
        safe["release"] = str(event["release"])[:120]

    tags = event.get("tags")
    if isinstance(tags, dict):
        sanitized_tags = safe_metadata(tags)
        if sanitized_tags:
            safe["tags"] = sanitized_tags

    values = []
    for raw in ((event.get("exception") or {}).get("values") or []):
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {
            "type": str(raw.get("type") or "Error")[:120],
            "value": str(raw.get("type") or "Error")[:120],
        }
        frames = []
        stacktrace = raw.get("stacktrace") or {}
        for frame in stacktrace.get("frames") or []:
            if not isinstance(frame, dict):
                continue
            frames.append(
                {
                    "filename": str(frame.get("filename") or "")[-200:],
                    "function": str(frame.get("function") or "")[:160],
                    "lineno": int(frame.get("lineno") or 0),
                }
            )
        if frames:
            item["stacktrace"] = {"frames": frames}
        values.append(item)
    if values:
        safe["exception"] = {"values": values}
    else:
        # Never forward the original Sentry message; event identity lives in the
        # allowlisted `event` tag instead.
        safe["message"] = "SanitizedTechnicalEvent"

    # Explicitly do not forward request/user/breadcrumbs/contexts/extra/logentry,
    # environment dumps, headers, message bodies, captions, cookies or secrets.
    return safe


def init_optional_sentry() -> bool:
    global _INITIALIZED
    if _INITIALIZED:
        return True
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        return False
    sentry_sdk.init(
        dsn=dsn,
        before_send=scrub_event,
        send_default_pii=False,
        max_breadcrumbs=0,
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        debug=False,
        default_integrations=False,
    )
    _INITIALIZED = True
    return True


def observe(event: str, *, level: str = "info", **metadata: Any) -> dict[str, str]:
    """Emit privacy-safe structured metadata and optionally a sanitized Sentry event.

    Routine successful lifecycle transitions stay in structured logs. Warning/error
    transitions are also sent to Sentry when the optional integration is enabled.
    """
    values = safe_metadata({"event": event, **metadata})
    # `pending_translation` is a normal in-flight lifecycle state. Provider failures
    # emit a separate `translation_deferred` warning, so do not flood Sentry merely
    # because an item has entered the translation stage.
    if (
        level == "warning"
        and values.get("event") == "update_lifecycle"
        and values.get("status") == "pending_translation"
        and not values.get("error_class")
    ):
        level = "info"
    log_level = logging.WARNING if level == "warning" else logging.ERROR if level == "error" else logging.INFO
    logger.log(log_level, "OBS %s", json.dumps(values, sort_keys=True, separators=(",", ":")))
    if _INITIALIZED and level in {"warning", "error"}:
        try:
            import sentry_sdk

            with sentry_sdk.push_scope() as scope:
                for key, value in values.items():
                    scope.set_tag(key, value)
                sentry_sdk.capture_message("SanitizedTechnicalEvent", level=level)
        except Exception:
            pass
    return values


def capture_technical_exception(
    exc: BaseException,
    *,
    component: str = "runtime",
    stage: str = "runtime",
    **metadata: Any,
) -> None:
    observe(
        "technical_exception",
        level="error",
        component=component,
        stage=stage,
        status="failed",
        error_class=type(exc).__name__,
        **metadata,
    )
