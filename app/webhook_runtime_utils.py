from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit


def derive_runtime_secret(token: str) -> str:
    """Derive a stable Telegram-compatible secret from the bot token."""
    raw = ("jeonghan-assistant-webhook-v1:" + str(token or "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalized_origin(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "", "", "")).rstrip("/")


def maintenance_url_from_webhook(
    webhook_url: str,
    *,
    trusted_origins: list[str] | tuple[str, ...] | None = None,
) -> str:
    parsed = urlsplit(str(webhook_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""

    trusted = {
        origin
        for origin in (_normalized_origin(item) for item in (trusted_origins or ()))
        if origin
    }
    current_origin = _normalized_origin(webhook_url)
    if trusted and current_origin not in trusted:
        return ""

    path = parsed.path or "/"
    suffix = "/telegram/webhook"
    if path.endswith(suffix):
        path = path[: -len(suffix)] + "/maintenance"
    else:
        path = "/maintenance"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
