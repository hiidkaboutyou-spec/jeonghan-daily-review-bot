# Daily watchdog transport safety

This note is durable Stage C project state for the Daily watchdog artifact transport.

## Current ownership

The credential-safe GitHub Actions artifact transport is owned by:

```text
tools/daily_watchdog_transport.py
```

The production workflow continues to execute the stable semantic CLI:

```text
python tools/daily_watchdog_runner.py
```

The runner explicitly calls `daily_watchdog_transport.install()` before delegating to `tools.daily_watchdog.main()`. Importing the semantic transport module itself is side-effect free.

The historical `tools/daily_watchdog_hardening.py` path remains present as an executable compatibility shim. Importing that historical path intentionally preserves its old installation side effect, but the transport implementation is no longer duplicated there.

## Why the hardened transport exists

The production watchdog downloads the `production-outcome` GitHub Actions artifact. GitHub's artifact archive endpoint responds with a redirect to a short-lived signed download URL. Python documents that ordinary `Request` headers are also added to redirected requests, so the authenticated GitHub API request and signed-host request require an explicit credential boundary.

The hardening was introduced after a real production artifact-download failure. Stage C later separated the stable production runner from the historical module name, then moved transport ownership into the semantic transport module only after direct regression coverage existed.

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

`tests/test_daily_watchdog_runner.py` separately proves that the production runner installs the semantic transport before calling the decision engine while remaining side-effect free on import.

`tests/test_daily_watchdog_hardening.py` is now a compatibility test. It proves that the historical module re-exports the canonical transport symbols, still reinstalls the protected method idempotently for old importers, and is not the active workflow command.

## Research basis

GitHub's current REST documentation for downloading an Actions artifact states that the endpoint returns a redirect URL, exposes that target in `Location`, and reports `302` for the download endpoint. Python's current `urllib.request` documentation states that headers added with the ordinary request header mechanism are also added to redirected requests, while unredirected headers are distinct. Those behaviors are the reason this code keeps redirect handling explicit rather than relying on transparent authenticated redirects.

No third-party HTTP dependency is added. The Python standard library is sufficient for the existing transport contract, and adding another HTTP stack would expand production/security surface without solving a demonstrated gap.

## Compatibility-shim removal gate

Do not delete `tools/daily_watchdog_hardening.py` in the ownership-migration change. Removal belongs to a later focused pull request only after all of the following are true:

- no production workflow, CLI, Python import, config, documentation command, or operator path still requires the historical module;
- structural and manual reference scans are clean, including non-Python workflow references that LibCST cannot see;
- semantic transport, runner, compatibility, project, maintenance, security, CodeQL, fanfic/benchmark, and exact production-image checks are green;
- merged `main` completes a real production monitor pass with state/database restore and persistence, encrypted recovery backup, and production outcome upload;
- the workflow-triggered Daily watchdog succeeds using the semantic runner after the ownership migration.

The compatibility shim must survive at least this ownership stage even if ordinary import graphs no longer require it.
