# Daily watchdog semantic entrypoint migration

This is durable Stage C state for the compatibility migration started after the transport safety net in PR #81.

## Decision

The production workflow uses `python tools/daily_watchdog_runner.py` as its active command. The historical `tools/daily_watchdog_hardening.py` remains present and continues to own the already-tested cross-origin artifact transport implementation during this compatibility phase.

The semantic runner is intentionally thin. It imports the canonical `tools.daily_watchdog`, explicitly installs the hardened `fetch_latest_production_outcome` implementation, and then delegates to `daily_watchdog.main()`. The explicit assignment makes installation idempotent even when Python's module cache means the compatibility module has already been imported.

## Why this is staged instead of a one-shot move

`daily_watchdog_hardening.py` contains a security-sensitive redirect boundary. Moving its implementation into the canonical module and deleting the historical path in the same change would combine workflow migration, transport refactoring, compatibility removal, and security behavior changes. The migration therefore separates naming/entrypoint cleanup from transport ownership changes.

No dependency or external repository is added. The Python standard library is sufficient for the proven transport behavior, and a new HTTP dependency would increase migration risk without addressing a demonstrated requirement.

## Compatibility contract

- `tools/daily_watchdog_runner.py` is the preferred production CLI entrypoint.
- `tools/daily_watchdog_hardening.py` must remain executable during this phase.
- The redirect/auth contract in `docs/watchdog-transport-safety.md` remains authoritative.
- Importing `daily_watchdog_runner` alone must not mutate the canonical watchdog client.
- Calling `daily_watchdog_runner.main()` must install the hardened fetch method before delegating to the canonical watchdog main function.
- The workflow must have the semantic runner as its active `run:` command.

## Assertion cleanup completed

The follow-up cleanup after PR #83 migrates the old broad workflow-string assertion in `tests/test_daily_watchdog_hardening.py` to inspect active `run:` lines. The test now requires the semantic runner and rejects the historical hardening path as an active workflow command. The temporary commented legacy command has therefore been removed from `.github/workflows/daily-watchdog.yml`.

This cleanup intentionally does not move, rewrite, or duplicate the artifact-download implementation. It changes no watchdog decision logic, schedules, secrets, state/database behavior, Telegram delivery, recovery action, or dependency.

## Next stage

The next focused stage may evaluate moving the hardened artifact transport implementation into `tools/daily_watchdog.py`. Before doing so:

- preserve every redirect/auth invariant from `docs/watchdog-transport-safety.md`;
- migrate or duplicate the direct transport regression tests so they protect the canonical implementation before ownership changes;
- keep `tools/daily_watchdog_hardening.py` as a compatibility shim until all callers and tests use the canonical implementation;
- avoid changing watchdog recovery/decision behavior in the same PR;
- require project validation, full tests/maintenance, security checks, and merged-main production/watchdog verification before deleting the compatibility module.

Do not delete the historical module merely because the workflow no longer invokes it directly.
