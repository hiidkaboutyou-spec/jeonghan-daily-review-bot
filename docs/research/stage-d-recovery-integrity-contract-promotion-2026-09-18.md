# Stage D recovery-integrity contract promotion — 2026-09-18

## Decision

Promote the single existing Import Linter contract for `app.x_recovery_integrity_runtime` from report-only observation to blocking maintenance enforcement.

Do not add a second architecture contract in this change.

## Evidence before promotion

The report-only pilot merged in PR #115 as `0bd30bc02f3a422d281af825c54edbdda8e43e16`.

Observed evidence:
- PR-head Maintenance #148: 1 contract kept, 0 broken, exit 0.
- Real-main Maintenance #149: 1 contract kept, 0 broken, exit 0.
- Security #157: static scan, production dependency audit, and maintenance dependency audit all green.
- Daily #4233 and scheduled Daily #4234: green.
- Watchdog #3338/#3339: green.
- No `ignore_imports` is present.
- Import Linter remains maintenance-only; production requirements/Docker are unchanged.

## Upstream semantic verification

The promotion decision re-checked Import Linter 2.15's current implementation, not only its prose documentation.

Reviewed upstream:
- Protected contract documentation:
  https://import-linter.readthedocs.io/en/stable/contract_types/protected/
- Protected contract implementation:
  https://github.com/seddonym/import-linter/blob/main/src/importlinter/contracts/protected.py
- Protected contract unit tests:
  https://github.com/seddonym/import-linter/blob/main/tests/unit/contracts/test_protected.py
- Release notes:
  https://import-linter.readthedocs.io/en/latest/release_notes/

Important semantic result:
- Protected contracts only inspect direct static import edges in the Grimp graph.
- With `as_packages=False`, both protected and allowed module expressions are treated as exact modules rather than packages.
- Therefore `allowed_importers = app` permits the `app` package initializer itself but does not automatically permit `app.*` descendants.
- This matches Hani's intended ownership boundary.

Import Linter 2.14 added `broken_contract_guidance`; the enforced contract now uses it to explain that the recovery-integrity installer must remain owned by `app/__init__.py`.

## Dynamic-import gap and companion gate

Static import graphs do not prove the absence of runtime import bypasses. Promotion therefore adds a separate AST-based audit in `tools/protected_import_bypass_audit.py`.

The audit scans `app/`, `tools/`, and `tests/` and blocks literal references to the protected module when used through common dynamic-loading surfaces, including:
- `importlib.import_module` and aliases;
- `builtins.__import__` and aliases;
- `runpy.run_module`;
- `pkgutil.resolve_name`;
- `importlib.util.spec_from_file_location`;
- source/sourceless file loaders with a literal protected module name;
- literal import code passed to `exec`/`eval`/`compile`;
- literal `sys.modules` access.

It also:
- resolves simple literal concatenation and unambiguous string constants;
- treats parse errors as blocking;
- never mutates source;
- explicitly documents that fully computed runtime names still require ordinary code/security review.

Focused unit tests use synthetic temporary files to prove clean static ownership remains allowed and representative dynamic bypasses are detected.

## Enforcement change

Maintenance CI now has two blocking architecture gates for the same single invariant:
1. Import Linter protected contract — static direct imports.
2. Protected import bypass audit — literal dynamic loading/access.

The Import Linter command continues to use `--no-cache` and no `ignore_imports`.

A contract break writes `import-linter-report.txt` and exits non-zero. Dynamic bypass evidence writes JSON and Markdown reports and exits non-zero.

## Workflow trigger correction

The pilot accidentally listed `.importlinter` twice under pull-request paths and omitted it from push paths.

Promotion normalizes this:
- exactly one `.importlinter` pull-request trigger;
- one `.importlinter` push-to-main trigger.

This ensures a contract-only change is validated both before merge and on real `main`.

## Promotion gates

Merge only if:
- focused dynamic-bypass audit tests pass;
- repository-wide dynamic-bypass audit reports zero violations and zero parse errors;
- Import Linter reports the one contract kept and exits 0;
- Maintenance dependency vulnerability audit remains green;
- Workflow Safety, Security, Daily, Fanfic/benchmark and other triggered required checks are green;
- no runtime/state/provider/delivery code changes appear in the diff.

After merge:
- require a real-main Maintenance run proving both architecture gates;
- require the normal real-main production proof triggered by the merge;
- keep the architecture queue at one contract; do not add a second contract in the same follow-up.

## Protected invariants

Promotion must not change:
- `x_retrieval_checkpoints`;
- checkpoint identity/version/normalization;
- retry/fallback accounting;
- source authorization;
- cursor advancement;
- recovery import-time patch order;
- provider behavior;
- Telegram/AO3 delivery;
- schedules or secrets;
- production dependencies.
