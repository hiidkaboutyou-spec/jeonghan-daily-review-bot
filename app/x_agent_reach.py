from __future__ import annotations

"""Bounded Agent Reach / twitter-cli fallback for degraded X recovery.

Agent Reach is a capability selector/health layer; its Twitter backend is the
upstream ``twitter-cli`` executable. Daily Hani invokes that backend directly only
after the existing public syndication recovery path fails. The primary twscrape
collector remains authoritative.

Security boundary: only the existing X_COOKIE auth_token/ct0 pair is exposed to the
child process. Telegram/model secrets are not inherited, and the child receives an
isolated HOME/XDG config tree so twitter-cli cannot fall back to local browser-cookie
stores when explicit credentials are invalid.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import MediaItem, Update, ensure_utc

DEFAULT_LIMIT = 200
MAX_LIMIT = 300
DEFAULT_TIMEOUT_SECONDS = 25
MAX_TIMEOUT_SECONDS = 60


class AgentReachXError(RuntimeError):
    pass


@dataclass(slots=True)
class AgentReachResult:
    updates: list[Update]
    raw_seen: int


def agent_reach_fallback_enabled() -> bool:
    value = os.environ.get("X_AGENT_REACH_FALLBACK_ENABLED", "1").strip().casefold()
    return value not in {"0", "false", "no", "off", "disabled"}


def _positive_int_env(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, maximum))


def _require_backend() -> str:
    if importlib.util.find_spec("agent_reach") is None:
        raise AgentReachXError("Agent Reach is not installed")
    executable = shutil.which("twitter")
    if not executable:
        raise AgentReachXError("Agent Reach Twitter backend (twitter-cli) is not installed")
    return executable


def _child_env(cookies: dict[str, str], isolated_home: str) -> dict[str, str]:
    auth_token = str(cookies.get("auth_token") or "").strip()
    ct0 = str(cookies.get("ct0") or "").strip()
    if not auth_token or not ct0:
        raise AgentReachXError("X_COOKIE is missing auth_token or ct0 for Agent Reach fallback")

    # Start from a narrow allow-list instead of inheriting every GitHub Actions secret.
    env: dict[str, str] = {}
    for name in (
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SYSTEMROOT",
        "WINDIR",
    ):
        value = os.environ.get(name)
        if value:
            env[name] = value

    env.update(
        {
            "HOME": isolated_home,
            "XDG_CONFIG_HOME": os.path.join(isolated_home, ".config"),
            "XDG_CACHE_HOME": os.path.join(isolated_home, ".cache"),
            "TWITTER_AUTH_TOKEN": auth_token,
            "TWITTER_CT0": ct0,
            "OUTPUT": "json",
            "NO_COLOR": "1",
            "TERM": "dumb",
        }
    )
    return env


def _safe_cli_error(text: str, cookies: dict[str, str]) -> str:
    cleaned = str(text or "").strip()
    for key in ("auth_token", "ct0"):
        secret = str(cookies.get(key) or "")
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    cleaned = " ".join(cleaned.split())
    return cleaned[:400] or "twitter-cli failed without a diagnostic"


def _media_items(raw_items: Any) -> list[MediaItem]:
    if not isinstance(raw_items, list):
        return []
    media: list[MediaItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        try:
            width = int(raw.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        try:
            height = int(raw.get("height") or 0)
        except (TypeError, ValueError):
            height = 0
        media.append(
            MediaItem(
                kind=str(raw.get("type") or "photo").casefold(),
                url=url,
                preview_url=url,
                width=max(0, width),
                height=max(0, height),
            )
        )
    return media


def _parse_payload(
    payload: dict[str, Any],
    *,
    handle: str,
    start: datetime,
    end: datetime,
) -> AgentReachResult:
    if payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "api_error")
        message = str(error.get("message") or "twitter-cli returned an error")
        raise AgentReachXError(f"twitter-cli {code}: {message}")

    rows = payload.get("data")
    if not isinstance(rows, list):
        raise AgentReachXError("twitter-cli JSON schema did not contain a tweet list")

    lower = ensure_utc(start)
    upper = ensure_utc(end)
    normalized = handle.lstrip("@").strip()
    updates: list[Update] = []
    source_seen = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("isRetweet") or row.get("isPromoted"):
            continue
        author_raw = row.get("author") if isinstance(row.get("author"), dict) else {}
        author = str(
            author_raw.get("screenName")
            or author_raw.get("username")
            or ""
        ).lstrip("@").strip()
        if author.casefold() != normalized.casefold():
            continue
        source_seen += 1

        identifier = str(row.get("id") or "").strip()
        created_raw = row.get("createdAtISO") or row.get("createdAt")
        if not identifier or not created_raw:
            continue
        try:
            created_at = ensure_utc(str(created_raw))
        except (TypeError, ValueError, OverflowError):
            continue
        if created_at < lower or created_at >= upper:
            continue

        quoted = row.get("quotedTweet") if isinstance(row.get("quotedTweet"), dict) else {}
        quoted_author_raw = (
            quoted.get("author") if isinstance(quoted.get("author"), dict) else {}
        )
        updates.append(
            Update(
                id=identifier,
                url=f"https://x.com/{author}/status/{identifier}",
                author=author,
                author_name=str(author_raw.get("name") or author),
                text=str(row.get("text") or "").strip(),
                created_at=created_at,
                quoted_id=str(quoted.get("id") or ""),
                quoted_text=str(quoted.get("text") or "").strip(),
                quoted_author=str(
                    quoted_author_raw.get("screenName")
                    or quoted_author_raw.get("username")
                    or ""
                ).lstrip("@").strip(),
                lang=str(row.get("lang") or ""),
                media=_media_items(row.get("media")),
                raw_query=f"agent-reach-twitter-cli:@{normalized}",
            )
        )

    if rows and source_seen == 0:
        raise AgentReachXError("twitter-cli returned rows outside the requested source authority")

    chosen = {item.id: item for item in updates if item.id}
    return AgentReachResult(
        updates=sorted(chosen.values(), key=lambda item: (item.created_at, item.id)),
        raw_seen=len(rows),
    )


def collect_agent_reach_timeline(
    cookies: dict[str, str],
    handle: str,
    start: datetime,
    end: datetime,
    *,
    include_replies: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> AgentReachResult:
    """Read one configured source through Agent Reach's twitter-cli backend.

    This is deliberately partial recovery. twitter-cli's structured user-posts schema
    does not expose a reliable reply-to field, so a source configured to exclude replies
    is not eligible for this fallback; returning unprovable rows would violate source
    policy.
    """
    if not agent_reach_fallback_enabled():
        raise AgentReachXError("Agent Reach fallback is disabled")
    if not include_replies:
        raise AgentReachXError("twitter-cli cannot prove reply exclusion for this source")

    normalized = str(handle or "").lstrip("@").strip()
    if not normalized or len(normalized) > 15 or not normalized.replace("_", "").isalnum():
        raise AgentReachXError("invalid X source handle")

    executable = _require_backend()
    bounded_limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    timeout = _positive_int_env(
        "X_AGENT_REACH_TIMEOUT_SECONDS",
        DEFAULT_TIMEOUT_SECONDS,
        MAX_TIMEOUT_SECONDS,
    )

    with tempfile.TemporaryDirectory(prefix="hani-agent-reach-") as isolated_home:
        env = _child_env(cookies, isolated_home)
        command = [
            executable,
            "user-posts",
            normalized,
            "--max",
            str(bounded_limit),
            "--json",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AgentReachXError(
                f"twitter-cli timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise AgentReachXError("twitter-cli could not be started") from exc

        if completed.returncode != 0:
            detail = completed.stderr or completed.stdout
            raise AgentReachXError(
                _safe_cli_error(detail, cookies)
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise AgentReachXError("twitter-cli returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise AgentReachXError("twitter-cli returned an unexpected JSON document")
        return _parse_payload(
            payload,
            handle=normalized,
            start=ensure_utc(start),
            end=ensure_utc(end),
        )
