from __future__ import annotations

import json
import platform
import urllib.error
import urllib.request


TARGETS = {
    "x_profile": "https://x.com/tesla",
    "syndication_profile": "https://syndication.twitter.com/srv/timeline-profile/screen-name/tesla",
    "publish_oembed": "https://publish.twitter.com/oembed?url=https://twitter.com/SpaceX/status/20",
}


def probe(url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/json",
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
        return {"reachable": False, "error_type": type(exc).__name__}

    lower = body.casefold()
    return {
        "status": status,
        "reachable": status < 400,
        "bytes_sampled": len(body.encode("utf-8", errors="ignore")),
        "cloudflare_challenge": "/cdn-cgi/challenge-platform/" in lower,
        "contains_tweet_data": any(
            marker in lower
            for marker in ("tweet-id", "timeline", "created_at", "data-tweet-id", "tweet_results")
        ),
    }


if __name__ == "__main__":
    print(json.dumps({
        "runner_os": platform.system().lower(),
        "targets": {name: probe(url) for name, url in TARGETS.items()},
    }, sort_keys=True))
