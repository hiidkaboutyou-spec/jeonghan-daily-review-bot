from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from app.config import parse_cookie_secret


def _summary(*, label: str, status: int, text: str) -> dict[str, Any]:
    lower = text.lower()
    return {
        "label": label,
        "status": int(status),
        "bytes": len(text.encode("utf-8", errors="ignore")),
        "has_cloudflare_challenge": "/cdn-cgi/challenge-platform/" in lower,
        "has_responsive_web": "/responsive-web/client-web/" in lower,
        "has_x_web": "/x-web/" in lower,
        "has_logged_out_entry": bool(re.search(r"entry-client-logged-out", text, re.I)),
    }


async def _request(label: str, *, cookies: dict[str, str] | None) -> dict[str, Any]:
    from twscrape.http import make_client

    client = make_client(headers={"user-agent": "@chrome"}, cookies=cookies or {})
    try:
        response = await client.get("https://x.com/tesla")
        return _summary(label=label, status=response.status_code, text=response.text)
    except Exception as exc:
        return {
            "label": label,
            "error_type": type(exc).__name__,
            "backend": getattr(client, "backend", "unknown"),
        }
    finally:
        await client.aclose()


async def diagnose() -> int:
    cookies = parse_cookie_secret(os.environ.get("X_COOKIE", ""))
    if not cookies:
        raise SystemExit("X_COOKIE is required")
    missing = [name for name in ("auth_token", "ct0") if not cookies.get(name)]
    if missing:
        print(json.dumps({"missing_required_cookie_names": missing}, sort_keys=True))
        return 2

    anonymous = await _request("anonymous", cookies=None)
    authenticated = await _request("authenticated", cookies=cookies)
    print(json.dumps({"anonymous": anonymous, "authenticated": authenticated}, sort_keys=True))

    # This diagnostic is observational: any HTTP result is useful evidence. Only
    # transport exceptions or missing required cookies make the probe itself fail.
    if "error_type" in anonymous or "error_type" in authenticated:
        return 3
    return 0


def main() -> int:
    return asyncio.run(diagnose())


if __name__ == "__main__":
    raise SystemExit(main())
