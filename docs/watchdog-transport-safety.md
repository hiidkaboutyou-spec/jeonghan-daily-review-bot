# Daily watchdog transport safety

This note is durable Stage C project state for `tools/daily_watchdog_hardening.py`.

## Why this wrapper exists

The production watchdog downloads the `production-outcome` GitHub Actions artifact. GitHub's artifact archive endpoint responds with a redirect to a short-lived signed download URL. The wrapper was introduced in PR #67 after a real production failure so the GitHub API request can use the repository bearer token while the redirected signed-host request does **not** inherit GitHub credentials.

The current production workflow directly executes:

```text
python tools/daily_watchdog_hardening.py
```

Therefore this file is a production CLI/workflow entrypoint even though it lives under `tools/` and has a historical-looking `hardening` name.

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
9. The production workflow must continue to point at the hardened entrypoint until a later focused migration explicitly changes and validates that workflow reference.

## Direct regression safety net

`tests/test_daily_watchdog_hardening.py` directly protects the wrapper rather than only the underlying `tools.daily_watchdog` decision engine. It covers:

- import-time installation of the hardened `fetch_latest_production_outcome` method;
- refusal of automatic redirects;
- authenticated direct artifact requests;
- credential stripping on cross-origin redirects;
- redirect status handling;
- missing `Location` failure;
- non-redirect HTTP errors;
- in-memory production-outcome ZIP parsing;
- bounded retry and final failure reporting;
- the workflow's direct hardened-entrypoint reference.

The test module scopes the wrapper's import-time monkey patch to its test class and restores the original method afterward so unrelated tests do not inherit test-order-dependent global state.

## Stage C decision

This safety-net step intentionally does **not** rename, move, rewrite, or simplify `tools/daily_watchdog_hardening.py`. It does not change `.github/workflows/daily-watchdog.yml`, runtime behavior, production dependencies, secrets, schedules, state, database, Telegram delivery, or recovery decisions.

No third-party library is added. The standard library is sufficient for the existing transport behavior, and introducing a new HTTP/refactor dependency would add risk without solving a demonstrated gap.

A later semantic migration may be considered only in a separate focused pull request after:

- these direct transport tests are green;
- all workflow/CLI/string references are inspected;
- a read-only structural plan is generated where applicable, with workflow references reviewed separately because LibCST only covers Python syntax;
- the old workflow entrypoint remains available through an explicit compatibility strategy during migration if needed;
- all project, security, maintenance, and exact production-image checks are green;
- the merged `main` watchdog and normal production run are verified without live-delivery side effects from PR validation.

Do not treat this file as a dead-code candidate merely because ordinary Python import graphs do not model its workflow/subprocess execution.
