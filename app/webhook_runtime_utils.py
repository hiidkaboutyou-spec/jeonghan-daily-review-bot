from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit


def derive_runtime_secret(token: str) -> str:
    """Derive a stable Telegram-compatible secret from the bot token."""
    raw = ("jeonghan-assistant-webhook-v1:" + str(token or "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def maintenance_url_from_webhook(webhook_url: str) -> str:
    parsed = urlsplit(str(webhook_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    suffix = "/telegram/webhook"
    if path.endswith(suffix):
        path = path[: -len(suffix)] + "/maintenance"
    else:
        path = "/maintenance"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
