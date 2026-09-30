from __future__ import annotations

import logging

from .config import ConfigError, Settings
from .telegram import TelegramBot

logger = logging.getLogger(__name__)


def run_preflight() -> dict[str, str]:
    """Validate live dependencies before the Render web process starts.

    This intentionally performs only bounded, read-only checks. Telegram access is
    required because the assistant cannot receive or reply without it. Gemini is
    optional: CaptionWriter already has a deterministic manual-review fallback, so
    missing/unavailable Gemini must degrade translation quality without taking the
    Telegram webhook offline.
    """
    settings = Settings.load(require_secrets=True)
    errors = settings.validate_files()
    if errors:
        raise ConfigError("; ".join(errors))
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

    gemini_status = "fallback (GEMINI_API_KEY is not configured)"
    if settings.gemini_api_key:
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
            gemini_status = f"fallback ({type(exc).__name__})"
        else:
            gemini_status = settings.gemini_model

    logger.info(
        "Production preflight passed: Telegram bot=%s review_chat=%s Gemini=%s X-cookies=%s",
        me.get("username") or me.get("id"),
        settings.review_chat_id,
        gemini_status,
        "present" if settings.x_cookies else "missing",
    )
    return {
        "telegram": "ok",
        "review_chat": "ok",
        "gemini": gemini_status,
        "x_cookie": "ok" if settings.x_cookies else "offline",
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
