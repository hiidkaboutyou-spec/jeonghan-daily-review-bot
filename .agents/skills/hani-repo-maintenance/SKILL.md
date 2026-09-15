---
name: hani-repo-maintenance
description: Safely organize and clean Daily Hani using evidence-first repository inventory, conservative linting, dead-code/dependency reports, focused refactors, and full validation without destabilizing production.
---

# Hani repository maintenance

Use this skill for cleanup, organization, module moves, naming cleanup, dead-code review, dependency cleanup, or repository-structure work in Daily Hani.

## Safety priority

Production behavior is authoritative. A cleaner tree is never worth breaking collection, filtering, deduplication, persisted state, recovery, private review, Telegram delivery, or scheduled workflows.

Do not treat historical-looking module names as dead code. Hani intentionally composes several runtime layers through import side effects in `app/__init__.py`.

## Required workflow

1. Read `AGENTS.md` and `docs/repository-maintenance.md`.
2. Verify current `main` and task-relevant CI before changing structure.
3. Generate or inspect the repository structure inventory.
4. Use Ruff definite-error findings as a blocking signal.
5. Treat Ruff style/import-order, Vulture, and Deptry findings as candidates only.
6. For every candidate move/delete/rename, inspect direct and indirect references, tests, workflows, Docker/runtime entry points, optional imports, and persisted-state/config implications.
7. Change one coherent module family at a time on a focused branch.
8. Preserve compatibility shims when moving a still-public or import-sensitive module.
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

### Native structure inventory

Use `tools/repo_structure_inventory.py` to map module responsibilities, internal import edges, parse errors, and historical naming candidates. The inventory identifies where to investigate; it does not prescribe deletion.

## Refactor order

Prefer this order:

1. remove proven dead development-only files;
2. consolidate duplicate helpers with identical responsibility;
3. replace historical filenames with semantic names one family at a time;
4. introduce stable subpackages only after import order and compatibility are understood;
5. add architecture-boundary enforcement only after the target boundaries are stable.

## Forbidden cleanup shortcuts

- No bulk move of the whole `app/` tree.
- No broad `ruff --fix` or formatter sweep mixed with behavior changes.
- No automatic Vulture deletion.
- No automatic Deptry dependency removal.
- No deleting a phase/fix/hardening module because of its name alone.
- No modifying runtime state, secrets, delivery targets, or production schedules as part of cosmetic cleanup.
- No second self-healing or orchestration framework just to support repository organization.

## Evidence standard before removal

A deletion or dependency removal should have all applicable evidence:
- no runtime/internal references after accounting for dynamic import patterns;
- no workflow/CLI/Docker references;
- focused regression coverage;
- full test/validation success;
- production smoke/live-provider/full-monitor success after merge when runtime code changed.
