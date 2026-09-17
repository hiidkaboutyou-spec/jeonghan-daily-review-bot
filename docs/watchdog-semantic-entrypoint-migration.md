# Daily watchdog semantic entrypoint migration

This is durable Stage C state for the watchdog compatibility migration started after the transport safety net in PR #81.

## Current decision

The production workflow uses:

```text
python tools/daily_watchdog_runner.py
```

as its active command.

The semantic runner imports the canonical watchdog decision engine plus the semantic artifact-transport owner:

```text
tools.daily_watchdog
tools.daily_watchdog_transport
```

and explicitly calls `daily_watchdog_transport.install()` before delegating to `daily_watchdog.main()`.

The historical `tools/daily_watchdog_hardening.py` compatibility path is retired. It no longer exists in the repository and must not be treated as a supported production or operator entrypoint. Its former compatibility behavior was removed only after the semantic runner and transport had proven stable in production and a fresh pre-removal LibCST plan found no production Python caller.

## Why this remained staged

The historical hardening module protected a security-sensitive redirect boundary. A one-shot rename/delete would have combined workflow migration, transport refactoring, compatibility removal, and security behavior changes.

The migration was therefore split into explicit stages:

1. PR #81 added direct regression coverage for the redirect/auth boundary.
2. PR #83 introduced the stable semantic production runner while retaining the historical implementation.
3. PR #84 removed the obsolete workflow assertion/comment debt after the semantic runner proved stable on `main`.
4. PR #85 moved transport ownership to `tools.daily_watchdog_transport`, added direct semantic transport coverage, retained the historical path as a compatibility shim, and made Render validation cover `tools/**` changes.
5. PR #86 is the later focused retirement change: it first generated a pre-removal LibCST plan while the shim still existed, then removed only the historical shim and its compatibility-only test after the plan showed no production Python caller.

No third-party dependency or external repository was added. GitHub's artifact-download API and Python's `urllib.request` behavior confirm that the explicit redirect boundary remains necessary, while the Python standard library remains sufficient to implement it.

## Stable contract after compatibility retirement

- `tools/daily_watchdog_runner.py` is the production CLI entrypoint.
- `tools/daily_watchdog_transport.py` is the semantic owner of credential-safe artifact transport.
- importing `tools.daily_watchdog_transport` alone is side-effect free;
- calling `daily_watchdog_transport.install()` installs the protected `fetch_latest_production_outcome` method on the canonical watchdog client;
- the redirect/auth contract in `docs/watchdog-transport-safety.md` remains authoritative;
- importing `daily_watchdog_runner` alone must not mutate the canonical watchdog client;
- calling `daily_watchdog_runner.main()` must install the semantic transport before delegating to the canonical watchdog main function;
- the workflow must keep the semantic runner as its active `run:` command;
- `tools.daily_watchdog_hardening` is retired and should not be recreated as an alternate runtime path.

## Regression safety net

`tests/test_daily_watchdog_transport_contract.py` owns the low-level redirect/auth/retry/ZIP regression contract for `tools.daily_watchdog_transport`.

`tests/test_daily_watchdog_runner.py` proves the runner installs that semantic transport before execution, remains side-effect free on import, keeps the workflow on the semantic runner, and guards the retirement of the historical hardening module path.

The watchdog decision/recovery engine in `tools/daily_watchdog.py` is unchanged by the compatibility retirement. No schedule, secret, state/database, provider, Telegram-delivery, recovery-decision, or production dependency behavior is changed.

## Compatibility retirement evidence

The ownership migration on `main` completed every deferred production gate before the shim was considered removable:

- `Jeonghan Daily Review Bot` #4039 completed restore, live-provider checks, a full automatic monitor pass, checkpoint, encrypted backup/upload, production-outcome upload, and state/database cache persistence;
- workflow-triggered `Jeonghan Daily Watchdog` #3128 succeeded through the semantic runner;
- post-merge `Render Production Validation` #254 succeeded on the ownership-migration commit;
- Maintenance, Security, and CodeQL were green.

PR #86 then generated a read-only LibCST plan before deleting the shim. The plan reported one reference total, zero structural references, one manual dynamic-string reference, zero import-order-sensitive references, and zero parse errors. The sole reference was the intentional compatibility import inside `tests/test_daily_watchdog_hardening.py`; there was no production Python importer.

The non-Python audit separately confirmed that `.github/workflows/daily-watchdog.yml` already used only `python tools/daily_watchdog_runner.py`, `README.md` had no historical hardening command, and remaining tracked references described only the compatibility/removal process. Detailed evidence, including the maintenance run/artifact identifiers and Deptry findings, is recorded in `docs/research/watchdog-hardening-shim-retirement-2026-09-17.md`.

## Future changes

Future watchdog transport work should modify the semantic transport and runner directly. Do not restore the historical hardening path merely for naming compatibility. If a previously unknown external/manual caller of the retired path is discovered, evaluate it explicitly; the old shim can be restored narrowly without reverting semantic ownership, but repository production behavior must remain anchored to the semantic runner.
