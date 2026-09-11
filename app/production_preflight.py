from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .config import ConfigError, Settings
from .telegram import TelegramBot, TelegramError
from .x_client import XCollectionError, XCollector

logger = logging.getLogger(__name__)


def _publish_github_provider_state(report: dict[str, str]) -> None:
    """Expose the live X probe to later steps without making X a hard dependency."""
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    if not github_env:
        return
    x_status = str(report.get("x", "offline (missing status)"))
    if x_status == "ok":
        state = "online"
    elif x_status.startswith("degraded"):
        state = "degraded"
    else:
        state = "offline"
    with Path(github_env).open("a", encoding="utf-8") as stream:
        stream.write(f"X_PROVIDER_PREFLIGHT={state}\n")


def _check_telegram(settings: Settings) -> str:
    """Fail closed only when the assistant cannot receive or send messages."""
    telegram = TelegramBot(
        settings.telegram_token,
        settings.admin_user_id,
        settings.review_chat_id,
    )
    try:
        me = telegram.api("getMe", timeout=20, attempts=2) or {}
        chat = telegram.api(
            "getChat",
            data={"chat_id": settings.review_chat_id},
            timeout=20,
            attempts=2,
        ) or {}
    except TelegramError as exc:
        raise ConfigError(f"Telegram production preflight failed: {type(exc).__name__}") from None
    if not isinstance(me, dict) or not me.get("id"):
        raise ConfigError("Telegram getMe preflight returned an invalid response.")
    if not isinstance(chat, dict) or not chat.get("id"):
        raise ConfigError("Telegram review chat is not accessible to the bot.")
    username = str(me.get("username") or "").strip().lstrip("@")
    return f"ok (@{username})" if username else "ok"


def _check_gemini(settings: Settings) -> str:
    if not settings.gemini_api_key:
        return "fallback (GEMINI_API_KEY is not configured)"
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=20_000),
        )
        model = client.models.get(model=settings.gemini_model)
        if model is None:
            raise RuntimeError("model lookup returned no data")
    except Exception as exc:
        return f"fallback ({type(exc).__name__})"
    return f"ok ({settings.gemini_model})"


async def _check_x(settings: Settings) -> str:
    missing = [name for name in ("auth_token", "ct0") if not settings.x_cookies.get(name)]
    if missing:
        return "offline (missing " + ", ".join(missing) + ")"
    collector = XCollector(settings.x_cookies, settings.sources, settings.keyword_groups)
    try:
        await collector.healthcheck()
    except XCollectionError:
        # Valid auth material exists, so a provider-wide web/client failure should not
        # disable collection completely. Runtime switches to bounded public syndication
        # and keeps the full-success cursor unchanged for later authenticated backfill.
        return "degraded (authenticated X unavailable; public fallback enabled)"
    except Exception:
        return "degraded (authenticated X unavailable; public fallback enabled)"
    return "ok"


async def run_preflight() -> dict[str, str]:
    """Check live providers without letting an optional provider kill Telegram."""
    settings = Settings.load(require_secrets=True)
    errors = settings.validate_files()
    if errors:
        raise ConfigError("; ".join(errors))

    telegram_status = _check_telegram(settings)
    return {
        "telegram": telegram_status,
        "x": await _check_x(settings),
        "gemini": _check_gemini(settings),
    }


def main() -> int:
    try:
        report = asyncio.run(run_preflight())
    except ConfigError as exc:
        logger.error("Production preflight failed: %s", exc)
        return 2

    for provider, status in report.items():
        print(f"Production preflight: {provider}={status}")
    _publish_github_provider_state(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
