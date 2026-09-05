from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import FastAPI

app = FastAPI(title="X egress probe", docs_url=None, redoc_url=None)


def _summary(status: int, text: str, backend: str) -> dict[str, Any]:
    lower = text.lower()
    return {
        "backend": backend,
        "status": int(status),
        "bytes": len(text.encode("utf-8", errors="ignore")),
        "has_cloudflare_challenge": "/cdn-cgi/challenge-platform/" in lower,
        "has_responsive_web": "/responsive-web/client-web/" in lower,
        "has_x_web": "/x-web/" in lower,
        "has_logged_out_entry": bool(re.search(r"entry-client-logged-out", text, re.I)),
    }


async def _probe() -> dict[str, Any]:
    from twscrape.http import make_client

    client = make_client(headers={"user-agent": "@chrome"})
    try:
        response = await client.get("https://x.com/tesla")
        return _summary(response.status_code, response.text, getattr(client, "backend", "unknown"))
    except Exception as exc:
        return {
            "backend": getattr(client, "backend", "unknown"),
            "error_type": type(exc).__name__,
        }
    finally:
        await client.aclose()


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "x-egress-probe", "status": "ready"}


@app.get("/probe")
async def probe() -> dict[str, Any]:
    # Anonymous only. Never accepts, stores, or returns X credentials/cookies.
    return await asyncio.wait_for(_probe(), timeout=20)
