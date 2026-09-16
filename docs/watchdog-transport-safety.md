# Daily watchdog transport safety

This note is durable Stage C project state for the Daily watchdog artifact transport.

## Why this transport exists

The production watchdog downloads the `production-outcome` GitHub Actions artifact. GitHub's artifact archive endpoint responds with a redirect to a short-lived signed download URL. The transport was introduced after a real production failure so the GitHub API request can use the repository bearer token while the redirected signed-host request does **not** inherit GitHub credentials.

GitHub documents the artifact download endpoint as returning a redirect URL whose signed target expires quickly. Python documents that ordinary `Request` headers are copied to redirected requests, while `add_unredirected_header` is specifically available for headers that must not be copied. The project keeps an explicit two-request boundary because it is easy to audit and already has direct regression coverage.

## Canonical implementation

As of the Stage C canonical-transport migration, the implementation lives in:

```text
tools/daily_watchdog_transport.py
```

The historical path remains as a deliberately thin compatibility entrypoint:

```text
tools/daily_watchdog_hardening.py
```

The compatibility module delegates to the canonical transport and preserves the historical private module surface used by the regression tests. It contains no independent transport algorithm.

The production workflow intentionally continues to execute:

```text
python tools/daily_watchdog_hardening.py
```

for this migration step. That means production reaches the new canonical implementation through the compatibility shim while the workflow/CLI contract remains unchanged. Switching the workflow to `daily_watchdog_transport.py` is a separate later migration gate, after the canonical module has proven stable on merged `main`.

## Required transport contract

Any later refactor or semantic rename must preserve all of these properties:

1. The initial GitHub API artifact request is a bounded `GET` using the existing timeout and may carry `Authorization`, `Accept`, `X-GitHub-Api-Version`, and the watchdog `User-Agent`.
2. Automatic redirect following is disabled for that authenticated request.
3. For supported redirect responses (301, 302, 303, 307, 308), the `Location` target is fetched with a new request.
4. The redirected signed-host request carries the watchdog `User-Agent` only. It must not forward `Authorization`, `Accept`, or `X-GitHub-Api-Version` from the GitHub API request.
5. A redirect without `Location` fails closed instead of guessing a destination.
6. A non-redirect HTTP error is not hidden by the low-level download helper.
7. Outcome-artifact lookup/retry remains bounded to the existing three attempts and one-second retry delay.
8. The downloaded archive is processed in memory and must contain `production-outcome.json` before JSON is returned.
9. The historical workflow entrypoint must remain available until a later focused workflow migration explicitly changes and validates that reference.

## Direct regression safety net

`tests/test_daily_watchdog_hardening.py` protects the transport contract through the compatibility entrypoint. It covers import-time installation, refusal of automatic redirects, authenticated direct artifact requests, credential stripping on redirects, redirect status handling, missing `Location`, non-redirect HTTP errors, in-memory ZIP parsing, bounded retry/final failure reporting, and the workflow's historical entrypoint reference.

The compatibility module deliberately re-exports the tested transport internals so this existing safety net exercises the canonical implementation rather than a duplicate copy.

## Stage C decision log

Completed:

- Added direct transport regression coverage before migration.
- Moved the credential-safe transport algorithm into `tools/daily_watchdog_transport.py`.
- Reduced `tools/daily_watchdog_hardening.py` to a compatibility shim.
- Kept the production workflow command unchanged for one stability stage.
- Added no third-party dependency: the Python standard library remains sufficient and avoids expanding production dependency/security surface.

Next eligible step:

- after this canonical module is green on PR and merged `main`, switch `.github/workflows/daily-watchdog.yml` to `python tools/daily_watchdog_transport.py` in a focused PR;
- keep the historical shim for at least one further stage so manual/operator references do not break;
- only consider deleting the shim after repository-wide reference/history checks and successful production watchdog runs using the canonical workflow entrypoint.

Do not treat either transport entrypoint as dead code merely because ordinary Python import graphs do not model workflow/subprocess execution.
