"""Read-only, redacted X health probe for an isolated GitHub runner matrix.

Never persist the cookie or account database beyond this process. The output is
limited to a coarse status; exception messages and response bodies are private.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path


def parse_cookies(raw: str) -> dict[str, str]:
    """Accept the existing JSON, Netscape, and Cookie-header secret formats."""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    if "\t" in raw and "\n" in raw:
        result = {}
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) >= 7 and not line.startswith("#"):
                result[parts[5]] = parts[6]
        return result
    parsed = SimpleCookie()
    try:
        parsed.load(raw)
    except Exception:
        return {}
    if parsed:
        return {key: morsel.value for key, morsel in parsed.items()}
    return dict(part.strip().split("=", 1) for part in raw.split(";") if "=" in part)


def failure_kind(exc: Exception) -> str:
    """Classify a failure without serializing URLs, cookies, or response text."""
    message = str(exc)
    if re.search(r"\b403\b", message):
        return "http_403"
    if re.search(r"\b401\b", message):
        return "http_401"
    if re.search(r"\b429\b", message):
        return "rate_limited"
    if "XClIdParseError" in message:
        return "transaction_id_unavailable"
    return "other_failure"


async def probe() -> dict[str, str]:
    cookies = parse_cookies(os.environ.get("X_COOKIE", ""))
    if not all(cookies.get(key) for key in ("auth_token", "ct0")):
        return {"authenticated_profile": "missing_credentials"}

    observed: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="x-runner-probe-") as tmp:
        try:
            await asyncio.wait_for(_read_profile(cookies, Path(tmp) / "accounts.sqlite3", observed), timeout=35)
        except asyncio.TimeoutError:
            return {"authenticated_profile": next(iter(observed), "timeout")}
        except Exception as exc:
            kind = failure_kind(exc)
            return {"authenticated_profile": kind if kind != "other_failure" else next(iter(observed), kind)}
    return {"authenticated_profile": "verified"}


async def _read_profile(cookies: dict[str, str], db_path: Path, observed: set[str]) -> None:
    from loguru import logger
    from twscrape import API

    # twscrape's default sink includes account identifiers and exception text.
    # Keep only a coarse in-memory status and never emit its raw diagnostics.
    logger.remove()

    def record_status(message) -> None:
        kind = failure_kind(RuntimeError(message.record["message"]))
        if kind != "other_failure":
            observed.add(kind)

    logger.add(record_status, level="WARNING")
    api = API(str(db_path), raise_when_no_account=True, wait_timeout=12, wait_interval=1)
    await api.pool.add_account_cookies(
        "reader_cookie", "; ".join(f"{key}={value}" for key, value in cookies.items())
    )
    if await api.user_by_login("pledis_17") is None:
        raise RuntimeError("X returned no profile data")


def main() -> int:
    result = asyncio.run(probe())
    print(json.dumps(result, sort_keys=True))
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write(f"Authenticated X profile probe: **{result['authenticated_profile']}**\n")
    return 0 if result["authenticated_profile"] == "verified" else 2


if __name__ == "__main__":
    raise SystemExit(main())
