# Watchdog hardening compatibility-shim retirement evidence — 2026-09-17

This note records the evidence used for the focused Stage C retirement of `tools/daily_watchdog_hardening.py`.

## Scope

The change removes only the historical compatibility path. It does not change the Daily watchdog decision engine, the semantic production runner, the credential-safe artifact transport, schedules, secrets, state/database handling, provider behavior, Telegram delivery, recovery decisions, or production dependencies.

Canonical surfaces remain:

- production CLI: `python tools/daily_watchdog_runner.py`
- decision engine: `tools.daily_watchdog`
- credential-safe artifact transport: `tools.daily_watchdog_transport`

## Proven baseline before removal

The removal branch started from `main` commit `703f72fdd40f22162878366a37e794e6459f5ad7`, the squash merge of PR #85.

The ownership migration had already completed every deferred production gate:

- `Jeonghan Daily Review Bot` run #4039 completed state/database restore, project validation, runtime smoke, live-provider checks, one full automatic monitor pass, database checkpoint, encrypted recovery backup creation/upload, production-outcome upload, and state/database cache persistence.
- workflow-triggered `Jeonghan Daily Watchdog` run #3128 completed successfully through `tools/daily_watchdog_runner.py`.
- post-merge `Render Production Validation` run #254 completed successfully and built the exact production image after `tools/**` was added to its path trigger.
- post-merge Maintenance, Security, and CodeQL were green.

## Pre-removal LibCST evidence

PR #86 deliberately added a read-only plan before deleting the shim. `Hani Maintenance Diagnostics` run `35235196181` generated artifact `10502977298` (`hani-maintenance-reports`, SHA-256 `19b0183343c97ebd78b69fa10ccf4a7ee40ecd70be540bbb0fa39a5adc3ccc4d`).

The generated `stage-c-watchdog-hardening-removal-plan.json` reported:

- old module exists: `true`
- semantic target exists: `true`
- compatibility shim required by the generic planner: `false`
- reference count: `1`
- structurally safe references: `0`
- manual-review references: `1`
- dynamic-string references: `1`
- import-order-sensitive references: `0`
- parse errors: `0`

The only Python reference was the intentional compatibility-test import:

`tests/test_daily_watchdog_hardening.py:17` → `importlib.import_module("tools.daily_watchdog_hardening")`

There was no production Python importer to migrate.

## Non-Python/manual audit

LibCST cannot prove workflow, shell, documentation, or operator-command safety, so those surfaces were inspected separately.

- `.github/workflows/daily-watchdog.yml` executes only `python tools/daily_watchdog_runner.py`.
- `README.md` contains no command using the historical hardening path.
- `docs/watchdog-semantic-entrypoint-migration.md` and `docs/watchdog-transport-safety.md` referenced the old path only to describe the compatibility phase/removal gate.
- `.agents/skills/hani-repo-maintenance/SKILL.md` still contained stale pre-#83 guidance claiming the hardening file was the production workflow entrypoint; that guidance is corrected as part of the retirement change.
- `docs/repository-maintenance.md` and `.github/workflows/maintenance-diagnostics.yml` still described the old direct hardening-wrapper Deptry exception; those references are updated as part of the retirement change.
- GitHub code search returned zero matches but marked its result incomplete, so it was not used as proof of absence.

No tracked workflow, config, or documented operator command requires the historical path after these focused updates. Untracked external/manual callers cannot be proven from repository evidence; if one is discovered, the shim can be restored without reverting the semantic runner or transport ownership.

## Direct-script import evidence and Deptry modeling

Python documents that `python path/to/script.py` prepends the script's directory to `sys.path`, while `python -m module` uses normal module execution semantics. The production workflow intentionally keeps the stable direct command `python tools/daily_watchdog_runner.py`.

For that reason, `daily_watchdog_runner.py` and `daily_watchdog_transport.py` retain their bounded direct-script fallback imports. Deptry sees the top-level fallback names `daily_watchdog` and `daily_watchdog_transport` as missing external dependencies even though they are first-party modules in `tools/`. Maintenance diagnostics therefore model exactly those two names as intentional `DEP001` first-party direct-script exceptions; this is independent of the retired hardening shim.

Relevant Python documentation:

- https://docs.python.org/3/using/cmdline.html
- https://docs.python.org/3/library/sys.html#sys.path

## Redirect/auth contract remains unchanged

Python also documents that ordinary `urllib.request.Request` headers are added to redirected requests. The semantic transport therefore continues to disable automatic redirect following for the authenticated GitHub API request and creates a fresh request for the signed artifact URL without repository authorization headers.

Relevant Python documentation:

- https://docs.python.org/3.11/library/urllib.request.html#urllib.request.Request.add_header
- https://docs.python.org/3.11/library/urllib.request.html#urllib.request.Request.add_unredirected_header

The full watchdog transport contract remains in `docs/watchdog-transport-safety.md` and is directly protected by `tests/test_daily_watchdog_transport_contract.py`.

## Retirement change

The focused removal performs only the compatibility cleanup:

1. delete `tools/daily_watchdog_hardening.py`;
2. delete the compatibility-only `tests/test_daily_watchdog_hardening.py`;
3. retain and strengthen runner coverage so the semantic production command remains authoritative and the historical module path remains absent;
4. correct maintenance Deptry modeling for the semantic direct-script runner/transport;
5. update stale migration, safety, repository-maintenance, and project-skill documentation.

The pre-removal plan is recorded here rather than kept as a permanent fifth full-repository LibCST scan after the historical file is gone.

## Required validation after removal

Before merge, the final removal head must pass focused/full project validation, Maintenance Diagnostics with per-test coverage, Security diagnostics, CodeQL, fanfic/translation checks, and exact Render production-image validation.

After merge, `main` must again complete a real production monitor pass with persistence/recovery steps and the workflow-triggered Daily watchdog must succeed through the semantic runner. Shim retirement is not considered proven in production until those post-merge checks are green.
