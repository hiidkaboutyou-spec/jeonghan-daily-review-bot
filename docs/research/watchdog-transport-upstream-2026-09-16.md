# Watchdog artifact transport upstream research — 2026-09-16

This note records the external evidence used for the Stage C watchdog transport ownership migration.

## GitHub Actions artifact download

GitHub's current REST documentation for **Download an artifact** states that the artifact archive endpoint returns a redirect URL, that the URL expires after one minute, and that clients can read the redirect target from the `Location` response header. The documented success response for the download endpoint is `302 Found`; `410 Gone` is also documented for unavailable/expired artifacts.

Project implication: the authenticated API request and the short-lived signed download request are separate transport steps. Hani keeps that redirect explicit instead of treating the signed target as another authenticated GitHub API request.

Reference: <https://docs.github.com/en/rest/actions/artifacts#download-an-artifact>

## Python urllib redirect/header behavior

Python's current `urllib.request` documentation states that headers added through the ordinary request header mechanism are also added to redirected requests. It separately documents `Request.add_unredirected_header()` for headers that must not be added to redirected requests, and `HTTPRedirectHandler.redirect_request()` as the hook that controls redirect requests.

Project implication: transparently following the artifact redirect while carrying ordinary authorization headers is not an acceptable credential boundary. Hani continues to disable automatic redirects for the authenticated request and constructs a fresh signed-target request carrying only the watchdog `User-Agent`.

References:

- <https://docs.python.org/3/library/urllib.request.html#urllib.request.Request.add_header>
- <https://docs.python.org/3/library/urllib.request.html#urllib.request.Request.add_unredirected_header>
- <https://docs.python.org/3/library/urllib.request.html#urllib.request.HTTPRedirectHandler.redirect_request>

## Dependency decision

No third-party HTTP package is adopted for this migration. The existing standard-library implementation already expresses the required credential boundary and is covered by direct regression tests. Replacing it during an ownership/name migration would add a second behavioral variable and expand the production dependency/security surface without solving a demonstrated problem.

## Architecture decision

The transport implementation is moved from the historical compatibility path `tools.daily_watchdog_hardening` to the semantic owner `tools.daily_watchdog_transport` rather than embedded in the watchdog decision engine. This keeps low-level HTTP redirect handling separate from recovery/dispatch decisions. The production CLI remains `tools/daily_watchdog_runner.py`, which explicitly installs the transport before invoking `tools.daily_watchdog.main()`.

The historical module remains as an executable compatibility shim for a later removal stage.
