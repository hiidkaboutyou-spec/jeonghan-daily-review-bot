from __future__ import annotations

import logging

from .config import ConfigError, Settings
from .telegram import TelegramBot

logger = logging.getLogger(__name__)


def run_preflight() -> dict[str, str]:
    """Validate live dependencies before the Render web process starts.

    This intentionally performs only bounded, read-only checks. If Telegram or
    Gemini credentials are invalid, deployment must fail instead of advertising a
    healthy assistant that can receive updates but cannot translate or reply.
    """
    settings = Settings.load(require_secrets=True)
    errors = settings.validate_files()
    if errors:
        raise ConfigError("; ".join(errors))
    if not settings.gemini_api_key:
        raise ConfigError("GEMINI_API_KEY is required for production webhook mode.")

    telegram = TelegramBot(
        settings.telegram_token,
        settings.admin_user_id,
        settings.review_chat_id,
    )
    me = telegram.api("getMe", timeout=20, attempts=2) or {}
    if not isinstance(me, dict) or not me.get("id"):
        raise ConfigError("Telegram getMe preflight returned an invalid response.")

    chat = telegram.api(
        "getChat",
        data={"chat_id": settings.review_chat_id},
        timeout=20,
        attempts=2,
    ) or {}
    if not isinstance(chat, dict) or not chat.get("id"):
        raise ConfigError("Telegram review chat is not accessible to the bot.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ConfigError("google-genai is unavailable in the production image.") from exc

    try:
        client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=20_000),
        )
        model = client.models.get(model=settings.gemini_model)
    except Exception as exc:
        raise ConfigError(
            f"Gemini preflight failed for {settings.gemini_model}: {type(exc).__name__}"
        ) from None
    if model is None:
        raise ConfigError(f"Gemini model {settings.gemini_model} is unavailable.")

    logger.info(
        "Production preflight passed: Telegram bot=%s review_chat=%s Gemini=%s X-cookies=present",
        me.get("username") or me.get("id"),
        settings.review_chat_id,
        settings.gemini_model,
    )
    return {
        "telegram": "ok",
        "review_chat": "ok",
        "gemini": settings.gemini_model,
        "x_cookie": "ok",
    }


def main() -> int:
    try:
        run_preflight()
    except ConfigError as exc:
        logger.error("Production preflight failed: %s", exc)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
