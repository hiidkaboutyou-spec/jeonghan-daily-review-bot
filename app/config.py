from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-lite"


class ConfigError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Missing config file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Config file must contain a JSON object: {path.relative_to(ROOT)}")
    return value


def parse_cookie_secret(raw: str) -> dict[str, str]:
    """Accept JSON cookies, a normal Cookie header, or Netscape rows."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError("X_COOKIE contains invalid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ConfigError("X_COOKIE JSON must be an object of cookie name/value pairs.")
        return {str(k): str(v) for k, v in parsed.items() if v is not None}
    if "\t" in raw and "\n" in raw:
        cookies: dict[str, str] = {}
        for line in raw.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
        return cookies
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception as exc:
        raise ConfigError("X_COOKIE could not be parsed as a Cookie header.") from exc
    parsed = {key: morsel.value for key, morsel in cookie.items()}
    if parsed:
        return parsed
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            parsed[key.strip()] = value.strip()
    return parsed


@dataclass(slots=True)
class Settings:
    telegram_token: str
    admin_user_id: int
    review_chat_id: int
    x_cookies: dict[str, str]
    gemini_api_key: str
    gemini_model: str
    timezone: ZoneInfo
    state_path: Path
    sources: list[dict[str, Any]]
    keyword_groups: list[dict[str, Any]]
    themes: dict[str, Any]
    runtime: dict[str, Any]

    @classmethod
    def load(cls, *, require_secrets: bool = True) -> "Settings":
        settings_json = read_json(ROOT / "config" / "settings.json")
        sources_json = read_json(ROOT / "config" / "sources.json")
        themes_json = read_json(ROOT / "config" / "themes.json")

        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        admin_raw = os.getenv("TELEGRAM_ADMIN_USER_ID", "").strip()
        chat_raw = os.getenv("TELEGRAM_REVIEW_CHAT_ID", "").strip()
        x_raw = os.getenv("X_COOKIE", "").strip()
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        if require_secrets:
            missing = [
                name
                for name, value in {
                    "TELEGRAM_BOT_TOKEN": token,
                    "TELEGRAM_ADMIN_USER_ID": admin_raw,
                    "TELEGRAM_REVIEW_CHAT_ID": chat_raw,
                    "X_COOKIE": x_raw,
                }.items()
                if not value
            ]
            if missing:
                raise ConfigError("Missing GitHub Actions secrets: " + ", ".join(missing))
        try:
            admin_id = int(admin_raw or 0)
            review_chat_id = int(chat_raw or 0)
        except ValueError as exc:
            raise ConfigError("Telegram IDs must be numeric.") from exc
        if require_secrets and admin_id <= 0:
            raise ConfigError("TELEGRAM_ADMIN_USER_ID must be a positive numeric Telegram user ID.")
        if require_secrets and review_chat_id == 0:
            raise ConfigError("TELEGRAM_REVIEW_CHAT_ID must be a non-zero numeric chat ID.")

        x_cookies = parse_cookie_secret(x_raw) if x_raw else {}
        if require_secrets:
            missing_x = [name for name in ("auth_token", "ct0") if not x_cookies.get(name)]
            if missing_x:
                raise ConfigError("X_COOKIE is missing required cookies: " + ", ".join(missing_x))

        timezone_name = str(settings_json.get("timezone", "Asia/Tehran"))
        try:
            timezone_info = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown timezone in config/settings.json: {timezone_name}") from exc

        configured_model = str(settings_json.get("gemini_model", DEFAULT_GEMINI_MODEL)).strip()
        gemini_model = os.getenv("GEMINI_MODEL", "").strip() or configured_model or DEFAULT_GEMINI_MODEL
        return cls(
            telegram_token=token,
            admin_user_id=admin_id,
            review_chat_id=review_chat_id,
            x_cookies=x_cookies,
            gemini_api_key=gemini_key,
            gemini_model=gemini_model,
            timezone=timezone_info,
            state_path=ROOT / str(settings_json.get("state_path", ".state/state.json")),
            sources=list(sources_json.get("sources", [])),
            keyword_groups=list(sources_json.get("keyword_groups", [])),
            themes=themes_json,
            runtime=dict(settings_json.get("runtime", {})),
        )

    def validate_files(self) -> list[str]:
        errors: list[str] = []
        handles: set[str] = set()
        for source in self.sources:
            handle = str(source.get("handle", "")).lstrip("@").strip()
            if not handle:
                errors.append("A source is missing its handle.")
                continue
            if not HANDLE_RE.fullmatch(handle):
                errors.append(f"Invalid X source handle: @{handle}")
                continue
            lowered = handle.lower()
            if lowered in handles:
                errors.append(f"Duplicate source: @{handle}")
            handles.add(lowered)
        if "couphanfiles" not in handles:
            errors.append("Required source @couphanfiles is missing.")
        if not any(str(term).strip() for group in self.keyword_groups for term in group.get("terms", [])):
            errors.append("No X keyword terms are configured.")
        required_categories = {
            "live",
            "jeonghan_instagram",
            "member_instagram",
            "brand",
            "fansign",
            "airport",
            "general",
        }
        missing_themes = sorted(required_categories - set(self.themes.get("themes", {})))
        if missing_themes:
            errors.append("Missing themes: " + ", ".join(missing_themes))
        return errors


def redact(value: str) -> str:
    if not value:
        return "<empty>"
    return value[:3] + "…" + value[-2:]
