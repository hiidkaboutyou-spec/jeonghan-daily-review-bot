---
name: hani-repo-maintenance
description: Safely organize and clean Daily Hani using evidence-first inventory, import-chain and per-test coverage evidence, syntax-aware read-only refactor plans, conservative diagnostics, focused compatibility-preserving changes, and full production validation.
---

# Hani repository maintenance

Use this skill for cleanup, organization, module moves, naming cleanup, dead-code review, dependency cleanup, complexity reduction, or repository-structure work in Daily Hani.

## Safety priority

Production behavior is authoritative. A cleaner tree is never worth breaking collection, filtering, deduplication, persisted state, recovery, private review, Telegram delivery, or scheduled workflows.

Do not treat historical-looking module names as dead code. Hani intentionally composes several runtime layers through import side effects. Stage B proved every historical-name module currently inside `app/` is runtime-linked.

## Required workflow

1. Read `AGENTS.md` and `docs/repository-maintenance.md`.
2. Verify current `main` and task-relevant CI before changing structure.
3. Generate or inspect the repository structure inventory and Stage B module-family evidence.
4. Before any rename/move, generate a read-only LibCST plan with `tools/module_refactor_plan.py`.
5. Treat Grimp import chains, Coverage.py per-test evidence, LibCST plan findings, Ruff style/import-order, Vulture, Deptry, and Complexipy as evidence/candidates only.
6. Inspect dynamic strings, workflows, Docker/runtime entry points, CLI/subprocess references, optional imports, persisted state/config, and `app/__init__.py` ordering separately.
7. Change one coherent module family at a time on a focused branch.
8. Preserve the old import path with a compatibility shim for active/import-sensitive modules until the migration has completed safely.
9. Run focused tests plus the full project validation suite.
10. Merge only with green checks, then verify the real production workflow on `main`.

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
- The planner is deliberately read-only: it distinguishes real import syntax from dynamic string references and never writes source files.
- Preserve comments/formatting and reason about imports structurally; never replace module names with regex or broad text substitution.
- `app/__init__.py` references are always import-order-sensitive and require explicit human/agent review.
- Dynamic string references are always manual-review findings and must never be auto-rewritten.
- LibCST does not authorize a refactor by itself. The Stage B graph, focused tests, compatibility shim, full CI, and post-merge production validation remain mandatory.

### Native structure inventory

Use `tools/repo_structure_inventory.py` to map module responsibilities, internal import edges, parse errors, and historical naming candidates. Use `tools/module_family_evidence.py` to combine historical-name candidates with Grimp import-chain evidence and optional per-test Coverage.py evidence. Use `tools/module_refactor_plan.py` to build a syntax-aware, non-mutating migration plan for one selected module. These reports identify what must be reviewed; they do not prescribe deletion or automatically apply changes.

## Deferred / rejected refactor tools

- **Rope:** active and capable, but its higher-level stateful rename/move engine is reference-only for now. Hani's import-time patch stack benefits from a narrower explicit LibCST plan before any transformation.
- **Bowler:** rejected. The upstream repository is archived and recommends LibCST for modern Python codemods.
- **Tach / Import Linter:** defer until Hani has stable intended package boundaries.
- **Pydeps:** defer unless visual cycle analysis becomes materially useful; Grimp plus the native inventory already provide Stage B dependency evidence.

## Stage C refactor order

Prefer this order:

1. add missing focused regression coverage for the selected active module;
2. generate a LibCST read-only refactor plan and inspect every manual/dynamic/order-sensitive reference;
3. choose a semantic target name based on responsibility, not historical development phase;
4. add the new implementation path while keeping the old path as a compatibility shim when needed;
5. update one importer family at a time, preserving import/install order;
6. run focused tests and full validation before removing any compatibility path;
7. remove the shim only in a later focused change after no runtime/workflow/config callers remain;
8. introduce stable subpackages only after module-family migrations prove the intended boundaries;
9. add architecture-boundary enforcement only after those boundaries are stable.

`live_recovery_hardening` is runtime-linked but previously lacked direct unit coverage. Add and keep focused tests for its fallback, outcome classification, degraded-source reconciliation, and idempotent installation before considering any rename/consolidation of that module.

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
- No modifying runtime state, secrets, delivery targets, or production schedules as part of cosmetic cleanup.
- No second self-healing or orchestration framework just to support repository organization.

## Evidence standard before removal

A deletion, move, rename, or dependency removal should have all applicable evidence:
- no unhandled runtime/internal references after accounting for dynamic import patterns;
- inspected Grimp entrypoint/import chains and LibCST refactor plan;
- no unresolved dynamic string/workflow/CLI/Docker references;
- focused regression coverage and inspected Coverage.py test contexts;
- preserved compatibility/import ordering during migration;
- full test/validation success;
- production smoke/live-provider/full-monitor success after merge when runtime code changed.
