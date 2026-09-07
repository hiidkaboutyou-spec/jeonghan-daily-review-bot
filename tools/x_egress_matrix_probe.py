from __future__ import annotations

import json
import platform
import urllib.error
import urllib.request


def probe(url: str = "https://x.com/tesla") -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(500_000).decode("utf-8", errors="ignore")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = exc.read(500_000).decode("utf-8", errors="ignore")
    except Exception as exc:
        return {
            "runner_os": platform.system().lower(),
            "reachable": False,
            "error_type": type(exc).__name__,
        }

    lower = body.casefold()
    return {
        "runner_os": platform.system().lower(),
        "status": status,
        "reachable": status < 400,
        "bytes_sampled": len(body.encode("utf-8", errors="ignore")),
        "cloudflare_challenge": "/cdn-cgi/challenge-platform/" in lower,
        "responsive_web": "/responsive-web/client-web/" in lower,
        "x_web": "/x-web/" in lower,
    }


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True))
