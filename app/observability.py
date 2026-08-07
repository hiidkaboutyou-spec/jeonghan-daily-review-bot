from __future__ import annotations

import os
from typing import Any

_INITIALIZED = False


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

    values = []
    for raw in ((event.get("exception") or {}).get("values") or []):
        if not isinstance(raw, dict):
            continue
        item: dict[str, Any] = {"type": str(raw.get("type") or "Error")[:120], "value": str(raw.get("type") or "Error")[:120]}
        frames = []
        stacktrace = raw.get("stacktrace") or {}
        for frame in stacktrace.get("frames") or []:
            if not isinstance(frame, dict):
                continue
            frames.append({
                "filename": str(frame.get("filename") or "")[-200:],
                "function": str(frame.get("function") or "")[:160],
                "lineno": int(frame.get("lineno") or 0),
            })
        if frames:
            item["stacktrace"] = {"frames": frames}
        values.append(item)
    if values:
        safe["exception"] = {"values": values}
    else:
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


def capture_technical_exception(exc: BaseException, *, component: str = "runtime") -> None:
    if not _INITIALIZED:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("component", str(component)[:80])
            # Never pass the original exception/message. Send only its class name.
            sentry_sdk.capture_message(f"TechnicalError:{type(exc).__name__}", level="error")
    except Exception:
        return
