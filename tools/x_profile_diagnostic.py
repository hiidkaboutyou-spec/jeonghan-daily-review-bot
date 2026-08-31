from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from app.config import parse_cookie_secret
from app.x_client import XCollector, normalize_handle


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def summarize_profile_response(payload: Any, handle: str) -> dict[str, str | bool]:
    """Return only non-sensitive profile identity/status metadata."""
    data = _mapping(_mapping(payload).get("data"))
    user = _mapping(data.get("user"))
    result = _mapping(user.get("result"))
    legacy = _mapping(result.get("legacy"))
    core = _mapping(result.get("core"))
    screen_name = str(legacy.get("screen_name") or core.get("screen_name") or "")
    rest_id = str(result.get("rest_id") or "")
    reason = str(
        result.get("reason")
        or result.get("unavailable_reason")
        or _mapping(result.get("unavailable_message")).get("text")
        or ""
    )
    normalized_screen_name = normalize_handle(screen_name).lower()
    expected = normalize_handle(handle).lower()
    return {
        "result_type": str(result.get("__typename") or "missing"),
        "reason": reason[:120],
        "has_numeric_id": bool(rest_id.isdigit()),
        "screen_name": normalized_screen_name,
        "exact_handle": bool(normalized_screen_name and normalized_screen_name == expected),
    }


async def diagnose(handle: str) -> int:
    cookies = parse_cookie_secret(os.environ.get("X_COOKIE", ""))
    if not cookies:
        raise SystemExit("X_COOKIE is required")
    with tempfile.TemporaryDirectory(prefix="x-profile-diagnostic-") as temp:
        collector = XCollector(cookies, [], [])
        collector.db_path = Path(temp) / "x.sqlite3"
        api = await collector._get_api()
        response = await api.user_by_login_raw(normalize_handle(handle))
        payload = response if isinstance(response, Mapping) else response.json()
    summary = summarize_profile_response(payload, handle)
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0 if summary["has_numeric_id"] and summary["exact_handle"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely diagnose one X profile response")
    parser.add_argument("handle")
    args = parser.parse_args()
    return asyncio.run(diagnose(args.handle))


if __name__ == "__main__":
    raise SystemExit(main())
