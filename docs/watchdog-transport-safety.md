# Daily watchdog transport safety

This note is durable Stage C project state for the Daily watchdog artifact transport.

## Current ownership

The credential-safe GitHub Actions artifact transport is owned by:

```text
tools/daily_watchdog_transport.py
```

The production workflow executes the stable semantic CLI:

```text
python tools/daily_watchdog_runner.py
```

The runner explicitly calls `daily_watchdog_transport.install()` before delegating to `tools.daily_watchdog.main()`. Importing the semantic transport module itself is side-effect free.

The historical `tools/daily_watchdog_hardening.py` compatibility shim is retired and no longer exists. Production and operator documentation must use the semantic runner rather than recreating or depending on the historical path.

## Why the hardened transport exists

The production watchdog downloads the `production-outcome` GitHub Actions artifact. GitHub's artifact archive endpoint responds with a redirect to a short-lived signed download URL. Python documents that ordinary `Request` headers are also added to redirected requests, so the authenticated GitHub API request and signed-host request require an explicit credential boundary.

The hardening was introduced after a real production artifact-download failure. Stage C later separated the stable production runner from the historical module name, moved transport ownership into the semantic transport module only after direct regression coverage existed, and retired the historical shim only after production and reference-removal gates were satisfied.

## Required transport contract

Any later refactor must preserve all of these properties:

1. The initial GitHub API artifact request is a bounded `GET` using the existing timeout and may carry `Authorization`, `Accept`, `X-GitHub-Api-Version`, and the watchdog `User-Agent`.
2. Automatic redirect following is disabled for that authenticated request.
3. For supported redirect responses (301, 302, 303, 307, 308), the `Location` target is fetched with a new request.
4. The redirected signed-host request carries the watchdog `User-Agent` only. It must not forward `Authorization`, `Accept`, or `X-GitHub-Api-Version` from the GitHub API request.
5. A redirect without `Location` fails closed instead of guessing a destination.
6. A non-redirect HTTP error is not hidden by the low-level download helper.
7. Outcome-artifact lookup/retry remains bounded to the existing three attempts and one-second retry delay.
8. The downloaded archive is processed in memory and must contain `production-outcome.json` before JSON is returned.
9. The production workflow continues to use the semantic runner; transport installation must happen before the watchdog decision engine executes.
10. Importing `tools.daily_watchdog_transport` alone must not mutate the canonical watchdog client.

## Direct regression safety net

`tests/test_daily_watchdog_transport_contract.py` protects the semantic transport owner directly. It covers:

- the semantic owner targeting the canonical watchdog module;
- refusal of automatic redirects;
- authenticated direct artifact requests;
- credential stripping on redirected requests;
- 301/302/303/307/308 handling;
- missing `Location` failure;
- non-redirect HTTP errors;
- in-memory production-outcome ZIP parsing;
- bounded retry and final failure reporting.

`tests/test_daily_watchdog_runner.py` separately proves that the production runner installs the semantic transport before calling the decision engine while remaining side-effect free on import. It also verifies the workflow uses the semantic runner and guards the retired historical hardening path from accidental reintroduction.

The former `tests/test_daily_watchdog_hardening.py` existed only to protect the compatibility phase and is removed together with the retired shim. The security behavior it once indirectly exercised remains covered directly by the semantic transport contract tests.

## Research basis

GitHub's REST documentation for downloading an Actions artifact states that the endpoint returns a redirect URL, exposes that target in `Location`, and reports `302` for the download endpoint. Python's `urllib.request` documentation states that headers added with the ordinary request header mechanism are also added to redirected requests, while unredirected headers are distinct. Those behaviors are the reason this code keeps redirect handling explicit rather than relying on transparent authenticated redirects.

Python also documents that direct execution with `python path/to/script.py` prepends the script's directory to `sys.path`. The production command intentionally remains `python tools/daily_watchdog_runner.py`, so the runner/transport retain narrow first-party fallback imports for direct-script execution. Maintenance diagnostics model those names explicitly rather than treating them as third-party dependencies.

No third-party HTTP dependency is added. The Python standard library is sufficient for the existing transport contract, and adding another HTTP stack would expand production/security surface without solving a demonstrated gap.

## Compatibility-shim retirement evidence

The historical shim survived the ownership migration and was removed only in a later focused Stage C change after all recorded gates were satisfied.

Before removal:

- the ownership-migration `main` run completed a real production monitor pass with state/database restore and persistence, encrypted recovery backup, and production-outcome upload;
- the workflow-triggered Daily watchdog succeeded through the semantic runner;
- post-merge exact Render production-image validation succeeded;
- a fresh read-only LibCST plan found exactly one Python reference, the intentional compatibility-test dynamic import, with no production importer and no parse error;
- workflow, README/operator command, maintenance, documentation, and project-skill references were inspected separately because LibCST cannot cover non-Python surfaces.

Detailed pre-removal evidence is recorded in `docs/research/watchdog-hardening-shim-retirement-2026-09-17.md`.

The old path should not be reintroduced as a second supported runtime surface. If an unknown external/manual caller is discovered later, evaluate that caller explicitly and restore only the minimal compatibility alias if production safety requires it.
