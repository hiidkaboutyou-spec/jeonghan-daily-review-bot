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

The historical `tools/daily_watchdog_hardening.py` path remains present only as an executable compatibility shim. It re-exports the semantic transport symbols and preserves the historical import-time installation behavior for old callers, but it no longer owns a duplicate transport implementation.

## Why this remained staged

The historical hardening module protected a security-sensitive redirect boundary. A one-shot rename/delete would have combined workflow migration, transport refactoring, compatibility removal, and security behavior changes.

The migration was therefore split into explicit stages:

1. PR #81 added direct regression coverage for the redirect/auth boundary.
2. PR #83 introduced the stable semantic production runner while retaining the historical implementation.
3. PR #84 removed the obsolete workflow assertion/comment debt after the semantic runner proved stable on `main`.
4. The transport-ownership stage adds direct tests against the semantic transport module first, then moves the implementation out of the historical module while retaining that path as a compatibility shim.

No third-party dependency or external repository is added. GitHub's current artifact-download API and Python's current `urllib.request` behavior confirm that the existing explicit redirect boundary remains necessary, while the Python standard library remains sufficient to implement it.

## Compatibility contract

- `tools/daily_watchdog_runner.py` is the preferred production CLI entrypoint.
- `tools/daily_watchdog_transport.py` is the semantic owner of credential-safe artifact transport.
- importing `tools.daily_watchdog_transport` alone is side-effect free;
- calling `daily_watchdog_transport.install()` installs the protected `fetch_latest_production_outcome` method on the canonical watchdog client;
- `tools/daily_watchdog_hardening.py` remains executable during this compatibility phase and preserves its historical import-time installation behavior;
- the redirect/auth contract in `docs/watchdog-transport-safety.md` remains authoritative;
- importing `daily_watchdog_runner` alone must not mutate the canonical watchdog client;
- calling `daily_watchdog_runner.main()` must install the semantic transport before delegating to the canonical watchdog main function;
- the workflow must keep the semantic runner as its active `run:` command.

## Ownership migration safety net

`tests/test_daily_watchdog_transport_contract.py` owns the low-level redirect/auth/retry/ZIP regression contract for `tools.daily_watchdog_transport`.

`tests/test_daily_watchdog_runner.py` proves the runner installs that semantic transport before execution and remains side-effect free on import.

`tests/test_daily_watchdog_hardening.py` is intentionally reduced to compatibility behavior: alias identity, idempotent installation, and confirmation that the historical path is not the active workflow command.

The watchdog decision/recovery engine in `tools/daily_watchdog.py` is unchanged by this ownership migration. No schedule, secret, state/database, provider, Telegram-delivery, recovery-decision, or production dependency behavior is changed.

## Next gate

Do not delete `tools/daily_watchdog_hardening.py` in the ownership-migration pull request. A later focused cleanup may remove the shim only after:

- Python imports, CLI/operator references, workflows, config, docs commands, and other string references have been inspected;
- LibCST evidence is clean for Python references and non-Python references are reviewed separately;
- semantic transport, runner, compatibility, project, maintenance, security, CodeQL, benchmark/fanfic, and exact production-image checks are green;
- merged `main` completes a normal production monitor pass with state/database restore and persistence, encrypted recovery backup, and production outcome upload;
- the workflow-triggered Daily watchdog succeeds using the semantic runner after the ownership migration;
- shim removal is performed in its own later pull request.

Do not treat the historical module as dead merely because the production workflow no longer invokes it directly.
