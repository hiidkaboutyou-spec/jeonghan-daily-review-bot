# Daily watchdog semantic entrypoint migration

This is durable Stage C state for the compatibility migration started after the transport safety net in PR #81.

## Decision

The production workflow now uses `python tools/daily_watchdog_runner.py` as its active command. The historical `tools/daily_watchdog_hardening.py` remains present and continues to own the already-tested cross-origin artifact transport implementation during this compatibility phase.

The semantic runner is intentionally thin. It imports the canonical `tools.daily_watchdog`, explicitly installs the hardened `fetch_latest_production_outcome` implementation, and then delegates to `daily_watchdog.main()`. The explicit assignment makes installation idempotent even when Python's module cache means the compatibility module has already been imported.

## Why this is staged instead of a one-shot move

`daily_watchdog_hardening.py` is a production entrypoint with a security-sensitive redirect boundary. Moving its implementation into the canonical module and deleting the historical path in the same change would combine workflow migration, transport refactoring, compatibility removal, and security behavior changes. This stage changes only the workflow-facing name while retaining the proven implementation unchanged.

No dependency or external repository is added. Research from the previous stage established that the Python standard library is sufficient and a new HTTP dependency would increase migration risk without addressing a demonstrated requirement.

## Compatibility contract

- `tools/daily_watchdog_runner.py` is the preferred production CLI entrypoint.
- `tools/daily_watchdog_hardening.py` must remain executable during this phase.
- The redirect/auth contract in `docs/watchdog-transport-safety.md` remains authoritative.
- Importing `daily_watchdog_runner` alone must not mutate the canonical watchdog client.
- Calling `daily_watchdog_runner.main()` must install the hardened fetch method before delegating to the canonical watchdog main function.
- The workflow must have the semantic runner as its active `run:` command.
- The temporary commented legacy command in the workflow exists only so the pre-migration regression assertion from PR #81 remains green until that assertion is migrated in a focused cleanup; a new test separately inspects active `run:` lines and forbids the legacy path as an active command.

## Next stage

After this migration is green on PR CI and merged `main`, the next focused stage should migrate the old workflow-string assertion in `tests/test_daily_watchdog_hardening.py` to the semantic runner, remove the temporary compatibility marker comment, and then evaluate moving the hardened transport implementation into `tools/daily_watchdog.py`. Only after equivalent direct transport tests protect the canonical implementation should `daily_watchdog_hardening.py` be reduced to a compatibility shim or removed.

Do not delete the historical module or duplicate the artifact-download implementation before those gates are satisfied.
