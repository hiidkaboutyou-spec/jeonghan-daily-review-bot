---
name: hani-repo-maintenance
description: Safely organize and clean Daily Hani using evidence-first inventory, import-chain and per-test coverage evidence, syntax-aware read-only refactor plans, conservative diagnostics, focused compatibility-preserving changes, and full production validation.
---

# Hani repository maintenance

Use this skill for cleanup, organization, module moves, naming cleanup, dead-code review, dependency cleanup, complexity reduction, or repository-structure work in Daily Hani.

## Safety priority

Production behavior is authoritative. A cleaner tree is never worth breaking collection, filtering, deduplication, persisted state, recovery, private review, Telegram delivery, or scheduled workflows.

Do not treat historical-looking module names as dead code. Hani intentionally composes several runtime layers through import side effects. Stage B shows the remaining historical implementation modules inside `app/` are runtime-linked. Active legacy compatibility shims may intentionally have no production importer; they are protected by `config/module_migrations.json` until their explicit removal gates pass.

## Required workflow

1. Read `AGENTS.md`, `docs/repository-maintenance.md`, `docs/module-migrations.md`, and `config/module_migrations.json`. If the task touches the Daily watchdog transport, runner, or the retired `tools/daily_watchdog_hardening.py` path, also read `docs/watchdog-transport-safety.md` and `docs/watchdog-semantic-entrypoint-migration.md` before changing code or workflow references.
2. Verify current `main` and task-relevant CI before changing structure.
3. Generate or inspect the repository structure inventory and Stage B module-family evidence.
4. Before any rename/move, generate a read-only LibCST plan with `tools/module_refactor_plan.py`.
5. Treat Grimp import chains, Coverage.py per-test evidence, LibCST plan findings, Ruff style/import-order, Vulture, Deptry, and Complexipy as evidence/candidates only.
6. Inspect dynamic strings, workflows, Docker/runtime entry points, CLI/subprocess references, optional imports, persisted state/config, `app/__init__.py` ordering, and active migration-registry contracts separately.
7. Change one coherent module family at a time on a focused branch.
8. Preserve the old import path with a compatibility shim for active/import-sensitive modules until the migration has completed safely.
9. Never remove a path registered as an active compatibility shim unless every recorded removal gate passes in a later focused pull request.
10. Run focused tests plus the full project validation suite.
11. Merge only with green checks, then verify the real production workflow on `main`.

## Tool roles

### Ruff

- CI/dev only.
- Blocking only for definite Python errors during the cleanup rollout.
- Import-order and formatting findings are report-only until an intentionally scoped formatting migration exists.
- Never run broad automatic fixes against production code without reviewing the exact diff.

### Vulture

- Report-only.
- Use confidence >= 90 for initial triage.
- Never delete code solely because Vulture reports it; dynamic imports, callbacks, monkey-patching, and import-time installers can look unused statically.

### Deptry

- Report-only.
- Verify optional imports, CLI-only paths, workflows, Docker behavior, and dynamically imported providers before removing a dependency.
- A package that appears unused in `app/` can still be required by a bounded fallback or subprocess integration.

### Complexipy

- Report-only.
- Use cognitive-complexity scores to rank functions that deserve human-reviewed simplification.
- A high score is not permission to rewrite a function. Preserve behavior and add focused tests before splitting or flattening logic.
- Do not apply generated refactor suggestions automatically.

### Coverage.py

- Maintenance-only; never a production dependency.
- Use `dynamic_context = test_function` so Stage B can see which concrete tests execute lines in historical modules.
- Do not introduce a repository-wide coverage percentage gate during structural cleanup; coverage here is evidence, not a vanity target.
- Zero measured coverage is not proof that a module is dead because import-time, subprocess, scheduled, or live-only paths may not be exercised by unit tests.

### Grimp

- Maintenance-only and report-only.
- Build the `app` import graph with type-checking-only imports excluded and no persistent graph cache.
- Use direct importers, downstream importers, upstream dependencies, and shortest entrypoint chains to understand blast radius.
- An absent static chain is not proof of no runtime use; dynamic imports and subprocess entry points still require direct inspection.

### LibCST

- Maintenance-only; never a production dependency.
- Use `tools/module_refactor_plan.py` before Stage C module renames/moves.
- The planner is deliberately read-only: it distinguishes real Python import references from comments/text and reports dynamic string references separately for human review.
- Preserve comments/formatting and reason about imports structurally; never replace module names with regex or broad text substitution.
- `app/__init__.py` references are always import-order-sensitive and require explicit human/agent review.
- Dynamic string references are always manual-review findings and must never be auto-rewritten.
- LibCST does not authorize a refactor by itself. The Stage B graph, focused tests, compatibility shim, migration registry, full CI, and post-merge production validation remain mandatory.

### Import Linter

- Maintenance-only; never a production runtime dependency.
- Stage D begins with one report-only protected contract in `.importlinter`.
- The pilot protects `app.x_recovery_integrity_runtime`: direct production import ownership stays with the `app` package initializer.
- Run with `--no-cache` and no `ignore_imports` during the pilot.
- A broken contract is evidence to investigate, not permission to rewrite imports.
- Do not add another contract until current Grimp evidence and tests prove the intended boundary already exists.
- Do not make the pilot blocking in the same PR that introduces it.

### Native structure inventory

Use `tools/repo_structure_inventory.py` to map module responsibilities, internal import edges, parse errors, and historical naming candidates. Use `tools/module_family_evidence.py` to combine historical-name candidates with Grimp import-chain evidence and optional per-test Coverage.py evidence. Use `tools/module_refactor_plan.py` to build a syntax-aware, non-mutating migration plan for one selected module. These reports identify what must be reviewed; they do not prescribe deletion or automatically apply changes.

## Deferred / rejected refactor tools

- **Rope:** active and capable, but its higher-level stateful rename/move engine is reference-only for now. Hani's import-time patch stack benefits from a narrower explicit LibCST plan before any transformation.
- **Bowler:** rejected. The upstream repository is archived and recommends LibCST for modern Python codemods.
- **Import Linter 2.15:** active Stage D report-only pilot; maintenance-only, one protected recovery-integrity ownership contract, `--no-cache`, no ignore rules.
- **Tach:** deferred alternative; do not install in parallel with the Import Linter pilot.
- **Pydeps:** defer unless visual cycle analysis becomes materially useful; Grimp plus the native inventory already provide Stage B dependency evidence.

## Stage C refactor order

Prefer this order:

1. add missing focused regression coverage for the selected active module;
2. generate a LibCST read-only refactor plan and inspect every manual/dynamic/order-sensitive reference;
3. choose a semantic target name based on responsibility, not historical development phase;
4. add the new implementation path while keeping the old path as a compatibility shim when needed;
5. update one importer family at a time, preserving import/install order;
6. register the legacy/canonical pair and explicit removal gates in `config/module_migrations.json`;
7. run focused tests and full validation before removing any compatibility path;
8. remove the shim only in a later focused change after no runtime/workflow/config callers remain and every registry gate passes;
9. introduce stable subpackages only after module-family migrations prove the intended boundaries;
10. add architecture-boundary enforcement only after those boundaries are stable.

Stage C is closed. `config/module_migrations.json` is authoritative and every registered legacy path is retired. Any future `compatibility-shim` status is a blocking maintenance error unless a new migration is explicitly researched, documented, and approved first. Do not recreate a retired path merely because a future caller or test still uses an old historical name; migrate the caller to the canonical module instead.

PR #97/#98 and the later focused retirement completed the human-quality-gate migration; PR #101–#104 completed quality-repair migration/retirement; PR #105 completed X-recovery coverage; PR #106 planned the recovery boundaries; PR #108/#110 migrated and retired the base implementation as `app.x_resumable_recovery_runtime`; PR #111 migrated the integrity layer to `app.x_recovery_integrity_runtime`; PR #112 retired the final registered compatibility path `app.phase3_recovery_hardening` and merged as `aa5cf158f84876557546af001985a6cca42e5943`. Independent real-main Daily #4221, Maintenance #138, Render #315, Security #146, CodeQL #107, Fanfic #987, and Watchdog #3324/#3325 passed. Fresh inventory leaves only `app.channel_part4_benchmark_hook`, `app.channel_part4_hardening`, `app.phase2_runtime_compat`, and `app.source_authority_hardening`; all are retain-by-design and runtime-linked. Preserve `x_retrieval_checkpoints`, CHECKPOINT_VERSION, checkpoint identity/normalization, retries, provider fallback, source authorization and cursor semantics. Do not reopen rename-oriented Stage C without new architecture evidence. Stage D may add only narrow, evidence-backed architecture contracts, beginning report-only and outside production dependencies.

The separate Daily-watchdog compatibility migration is documented in `docs/watchdog-semantic-entrypoint-migration.md`. Its historical `tools/daily_watchdog_hardening.py` shim was retired only after the semantic runner/transport had passed production validation and a dedicated pre-removal LibCST plan found no production Python caller. The detailed removal evidence is recorded in `docs/research/watchdog-hardening-shim-retirement-2026-09-17.md`.

## Watchdog transport gate

The production workflow uses `python tools/daily_watchdog_runner.py`; credential-safe artifact transport is owned by `tools/daily_watchdog_transport.py`. The historical `tools/daily_watchdog_hardening.py` path is retired and must not be treated as the current workflow entrypoint or recreated as a parallel implementation.

Any watchdog transport change must preserve the cross-origin artifact-download security/reliability contract documented in `docs/watchdog-transport-safety.md`: authenticate only the GitHub API request, prevent repository credentials from being forwarded to the signed redirect host, preserve supported 301/302/303/307/308 handling, bounded retries and ZIP parsing, keep transport import side-effect free, and install the transport explicitly from the semantic runner before the decision engine executes.

`tests/test_daily_watchdog_transport_contract.py` is the direct low-level transport safety net. `tests/test_daily_watchdog_runner.py` protects installation order, side-effect-free runner import, the workflow command, and the retirement of the historical hardening path. Inspect `.github/workflows/daily-watchdog.yml` separately from Python-only import tooling whenever the production entrypoint or recovery orchestration is touched.

The semantic runner is intentionally executed as a direct script, so its narrow `daily_watchdog` / `daily_watchdog_transport` first-party fallback imports remain part of the supported CLI behavior. Maintenance Deptry diagnostics model those fallback names explicitly rather than treating them as third-party packages.

## Forbidden cleanup shortcuts

- No bulk move of the whole `app/` tree.
- No broad `ruff --fix` or formatter sweep mixed with behavior changes.
- No regex/string-replace module rename.
- No automatic LibCST apply in CI or unattended source rewrite.
- No automatic Vulture deletion.
- No automatic Deptry dependency removal.
- No automatic Complexipy refactor application.
- No deletion based only on missing Coverage.py execution.
- No deletion based only on missing Grimp import chains.
- No deleting a phase/fix/hardening module because of its name alone.
- No deleting an active compatibility shim because it has zero production importers.
- No modifying runtime state, secrets, delivery targets, or production schedules as part of cosmetic cleanup.
- No second self-healing or orchestration framework just to support repository organization.

## Evidence standard before removal

A deletion, move, rename, or dependency removal should have all applicable evidence:
- no unhandled runtime/internal references after accounting for dynamic import patterns;
- inspected Grimp entrypoint/import chains and LibCST refactor plan;
- no unresolved dynamic string/workflow/CLI/Docker references;
- no active migration-registry gate left unsatisfied;
- focused regression coverage and inspected Coverage.py test contexts;
- preserved compatibility/import ordering during migration;
- full test/validation success;
- production smoke/live-provider/full-monitor success after merge when runtime code changed.
