# Repository maintenance and cleanup

This document defines the safe cleanup strategy for Daily Hani. The goal is to make the repository easier to understand and maintain without changing production behavior just to make filenames or folders look cleaner.

## Why cleanup must be incremental

The application has deliberate import-time installation layers. Some modules with historical names such as `phase*`, `part*`, `*fix`, or `*hardening` are active runtime components. A filename that looks temporary is therefore only a cleanup candidate, not evidence that the file is unused.

Production stability, state compatibility, collection completeness, deduplication, recovery, and private-review delivery remain higher priority than cosmetic structure.

## Audited maintenance tools

### Ruff 0.16.7 — adopted, CI/dev only

Repository: `astral-sh/ruff`
Audited release commit: `b5dba861cc38e3f7fb4524c9ceba3e01a474ea13`
License: MIT

Use now:
- block definite Python errors with `E9,F63,F7,F82`;
- produce non-blocking import-order and formatting reports;
- never run broad `--fix` automatically against production modules during the cleanup rollout.

### Vulture 2.16 — adopted, report only

Repository: `jendrikseipp/vulture`
Audited release commit: `b0f67ba0044693aa9ec0d38fe460590facc98004`
License: MIT

Use now:
- report dead-code candidates at confidence >= 90;
- never delete a symbol or module solely because Vulture reports it.

Hani uses import-time patch/install layers and dynamic behavior, so static dead-code analysis can produce false positives.

### Deptry 0.25.1 — adopted, report only

Repository: `osprey-oss/deptry`
Audited release commit: `0c39226d761685125c5ff71ca81282d6751c9540`
License: MIT

Use now:
- compare imports under the repository with `requirements.txt`;
- surface possibly unused, missing, or transitive dependencies;
- never remove a dependency until optional/dynamic imports, workflows, Docker, and production smoke are checked.

Known Hani static-analysis exceptions are documented for Agent Reach, `twitter-cli`, Uvicorn, and the semantic Daily-watchdog direct-script first-party fallbacks (`daily_watchdog` and `daily_watchdog_transport`). The watchdog exceptions exist because the production command is intentionally `python tools/daily_watchdog_runner.py`; they are not third-party dependencies and are no longer tied to the retired hardening shim. After those intentional entry points are modeled, the repository scan is expected to report no dependency issues.

### Complexipy 8.0.1 — adopted, report only

Repository: `rohaquinlop/complexipy`
Audited release commit: `030e2079457412221087f520445e9f2a709faad6`
License: MIT

Use now:
- rank cognitively complex functions in `app/`;
- keep the report non-blocking;
- use scores to choose where focused refactoring could improve maintainability;
- never apply generated refactor suggestions automatically.

### Coverage.py 7.16.1 — adopted for Stage B, CI/dev only

Repository: `coveragepy/coveragepy`
Audited release commit: `ccbb99245dcb4e51a35087285499cdb22b164ccc`
License: Apache-2.0

Use now:
- measure the existing `unittest` suite without introducing pytest;
- enable `dynamic_context = test_function` so individual test functions are recorded as execution contexts;
- generate temporary line-to-test evidence for `app/`;
- do not set a repository-wide coverage percentage gate during structural cleanup.

### Grimp 3.17 — adopted for Stage B, report only

Repository: `python-grimp/grimp`
Audited release commit: `286f0f5de79d29dea44cfa6563802e9b4fe37dea`
License: BSD-2-Clause

Use now:
- build a queryable import graph for the `app` package;
- exclude imports that exist only under `TYPE_CHECKING` from runtime evidence;
- inspect direct importers, dependencies, transitive impact, and shortest application-entrypoint chains;
- never infer that a module is dead solely because the static graph has no path to it.

### LibCST 1.9.0 — adopted for Stage C planning, CI/dev only

Repository: `Instagram/LibCST`
Audited release commit: `c029c17bf45a3737fc8d1347001ab2422f42ae58`
License: MIT with documented PSF/Apache-derived files

Use now:
- parse Python imports as concrete syntax rather than doing text/regex replacement;
- power `tools/module_refactor_plan.py`, a read-only module rename/move planner;
- distinguish structural imports from dynamic string references;
- identify references inside import-order-sensitive `app/__init__.py`;
- generate a migration plan without editing source files.

Decision: LibCST is installed only in `requirements-maintenance.txt`. Hani does **not** enable broad or unattended codemod application. A LibCST finding is migration evidence, not permission to rewrite code.

Rationale: Stage C needs syntax-aware refactoring assistance that preserves comments and Python structure. This is safer than regex replacement and narrower than introducing a stateful project-wide refactoring engine.

### Rope 1.14.0 — reference only

Repository: `python-rope/rope`
Reviewed release commit: `a32584f5742c093b10437f8da13bfefddb19c155`
License: LGPL-3.0

Rope is active and supports rename, move, import organization, preview, and other mature refactor operations. We are not installing it now. Its higher-level stateful project transformations are broader than Hani needs while historical runtime modules still depend on carefully ordered import-time installation. Reconsider only if focused LibCST migrations become insufficient.

### Bowler — rejected

Repository: `facebookincubator/Bowler`
Reviewed final branch commit: `92c9eeb7eebab8a1b65a989d0cf3b4947773ea2b`
License: MIT

The repository is archived/read-only. Its older fissix/lib2to3-oriented codemod stack is not a good new dependency for modern Hani refactors; upstream guidance points modern Python codemod work toward LibCST instead.

### Import Linter 2.15 — reference only for now

Repository: `seddonym/import-linter`
Reviewed commit: `31927f1457e3df673912cb5efb0afa6dbc37585f`
License: BSD-2-Clause

Decision: do not install yet. It is useful after packages have stable responsibilities. Adding architecture contracts now would freeze transitional coupling rather than describe the intended architecture.

### Tach 0.35.1 — reference only for later architecture enforcement

Repository: `tach-org/tach`
Reviewed release commit: `65df67ac51a8d0e8f9e0398ea72c924fea34fd25`
License: MIT

Tach can visualize/enforce dependencies, interfaces, and cycles, but its value depends on intentional stable module boundaries. Defer it to Stage D.

### Pydeps 3.0.8 — reference only

Repository: `thebjorn/pydeps`
Reviewed release commit: `6f73953ef47e6ff40c1fdfaf692c3d0bc47e3867`
License: BSD-2-Clause

Not installed because Hani's native inventory plus Grimp already provide the Stage B import evidence without a second Graphviz/display pipeline.

### Refurb 2.3.1 — rejected for the current cleanup stage

Repository: `dosisod/refurb`
Reviewed release commit: `0dbb127465ca9398b6c89c32a7fd86d78ca755c4`
License: GPL-3.0

Modernization/refactor suggestions overlap Ruff/Complexipy and would add another type-analysis false-positive surface. It is not installed.

## Native structure, Stage B evidence, and Stage C planning

`tools/repo_structure_inventory.py` provides a mutation-free map of `app/` and `tools/`:
- module path;
- coarse responsibility category;
- internal `app`/`tools` imports;
- historical phase/fix-style names;
- parse errors.

`tools/module_family_evidence.py` adds Stage B evidence:
- direct and transitive Grimp import relationships;
- shortest static chains from `app` and `app.__main__`;
- optional per-test Coverage.py execution contexts;
- conservative risk/review hints.

The initial real Stage B run found 13 historical-name candidates overall: 12 inside `app/` plus `tools.daily_watchdog_hardening`. At that time all 12 historical-name modules inside `app/` were runtime-linked, so none was eligible for immediate deletion. Stage C has since moved selected responsibilities to semantic modules and, only after separate removal gates, retired the watchdog hardening path and all four registered app compatibility shims. Their evidence remains durable rather than rewriting that historical Stage B finding.

`app.live_recovery_hardening` was particularly important in the initial evidence: it was connected to the real production entrypoint through `sentry_runtime`, but the Stage B unit-test run measured no direct coverage for it. The correct response was focused regression testing before migration, not removal.

`tools/module_refactor_plan.py` is the Stage C safety layer. Given an old and proposed new module name it:
- scans `app/`, `tools/`, and `tests/` with LibCST;
- identifies actual `import` / `from ... import ...` references;
- reports dynamic string references separately;
- marks `app/__init__.py` references as import-order-sensitive;
- distinguishes simple structural changes from cases requiring manual splitting/review;
- checks whether old/target paths exist;
- recommends a compatibility phase for active `app` modules;
- never mutates source files.

Example read-only use:

```bash
python tools/module_refactor_plan.py \
  --old-module app.some_historical_module \
  --new-module app.semantic_module_name \
  --json refactor-plan.json \
  --markdown refactor-plan.md
```

A clean plan still does not prove a move is safe. Dynamic imports, subprocess/CLI references, workflows, persisted config/state, and runtime-only paths require direct inspection.

## CI enforcement levels

`Hani Maintenance Diagnostics` uses three levels:

1. **Blocking:** Ruff definite Python errors, inventory/plan parse errors when invoked, and the existing unit suite when per-test Stage B coverage is collected.
2. **Report-only:** Grimp relationships, Coverage.py evidence, Ruff import ordering/format checks, Vulture candidates, Deptry findings, and Complexipy hotspots.
3. **Human/agent review:** every move, rename, deletion, dependency removal, complexity refactor, compatibility-shim removal, or package-boundary change.

Maintenance tooling is installed only in an ephemeral CI virtual environment from `requirements-maintenance.txt`. It is not part of the production Docker dependency graph.

## Cleanup roadmap

### Stage A — inventory and diagnostics

Completed. Hani has stable maintenance diagnostics without changing production runtime dependencies.

### Stage B — classify active module families

Completed. The real repository was measured with Grimp plus per-test Coverage.py evidence. Historical modules were treated as runtime-linked until later Stage C migrations could prove otherwise; Stage B never authorized direct deletion.

Key outcome: `live_recovery_hardening` required focused regression coverage because it was production-linked but had no direct measured unit coverage.

### Stage C — consolidate one family at a time

Current stage.

Rules:
- add missing focused tests before moving an active module;
- generate a LibCST read-only refactor plan before changing imports;
- use semantic responsibility names rather than phase/fix history;
- retain the old import path as a compatibility shim when the module is active/import-sensitive;
- preserve exact installation order where side effects matter;
- update one coherent importer family at a time;
- never combine unrelated module moves into one cleanup PR;
- remove compatibility shims only in later focused changes after all references and production behavior are proven safe.

The first Stage C guardrail was direct testing for `live_recovery_hardening`; no production module was renamed in the tooling/coverage PR that introduced LibCST. The Daily-watchdog tool family then demonstrated the full staged lifecycle: direct security regression coverage, semantic runner migration, semantic transport ownership, production proof, a pre-removal LibCST/manual audit, and later focused shim retirement.

The registered compatibility-shim subphase is production-complete. PR-triggered Maintenance #76 then performed the closure audit of the eight remaining historical-name implementation modules with fresh per-test Coverage contexts and current Grimp evidence. The durable decisions are:

**Retain by design**
- `app.channel_part4_benchmark_hook` — narrow cached-benchmark import-order contract; normal bot startup is intentionally unaffected.
- `app.channel_part4_hardening` — central fidelity layer with 5 direct / 13 downstream importers and high internal complexity; a filename-only migration would create churn without reducing coupling.
- `app.phase2_runtime_compat` — explicit state/test-double compatibility boundary; renaming would add schema/test risk without changing the underlying contract.
- `app.source_authority_hardening` — the current name accurately describes the configured-source authority policy layer and its `XCollector` hardening role.

**Migrate later**
- `app.channel_part4_humanfix` — stable human-quality-gate responsibility; strengthen less-exercised polish/client/fingerprint coverage before a semantic migration.
- `app.channel_part4_qualityfix` — stable quality-repair/fallback responsibility with very strong measured coverage; migrate only after the human-quality-gate path is stable.
- `app.phase3_recovery` — clear resumable X-recovery responsibility, but critical checkpoint/state/retry coverage must be strengthened before migration.
- `app.phase3_recovery_hardening` — clear recovery-integrity responsibility; plan it as the same architectural family as the recovery base and preserve patch/install order.

No remaining historical-name module is approved for direct deletion or unattended rename. Detailed coverage/import evidence, rationale, candidate semantic names and future sequencing are recorded in `docs/research/stage-c-closure-audit-2026-09-17.md`.

The future Stage C order is deliberately conservative: strengthen human-gate coverage, then consider the human-quality-gate migration; stabilize that dependency before quality-repair migration; separately strengthen critical recovery coverage and design the base/integrity recovery migration family. The four retain-by-design modules stay out of rename queues unless their underlying architecture changes.

PR #97/#98 and the later retirement completed the human-quality-gate migration; PR #101–#104 completed quality-repair migration/retirement; PR #105 completed the X-recovery coverage precursor. PR #106 then established the migration sequence from fresh LibCST/Grimp evidence: base recovery must move first to `app.x_resumable_recovery_runtime` under a same-module-object compatibility shim because both integrity hardening and completeness provider-proof patch that base module object at import time. Persisted key `x_retrieval_checkpoints`, checkpoint IDs/schema normalization, source-scoped retry semantics and cursor advancement contracts must not change. Only after independent production proof of the base move may `app.phase3_recovery_hardening` move separately to `app.x_recovery_integrity_runtime`.

### Stage D — enforce stable package boundaries

Only after the remaining explicitly deferred Stage C migrations have either completed or been intentionally deferred should Tach, Import Linter, or equivalent architecture contracts be reconsidered. The tool must describe the architecture we intentionally want, not freeze accidental historical coupling.

## Non-negotiable cleanup rules

- No bulk auto-fix across production code.
- No regex/string-replace module rename.
- No unattended LibCST/codemod apply.
- No automatic deletion from Vulture output.
- No dependency removal from Deptry output alone.
- No automatic rewrite from Complexipy or another refactoring recommender.
- No deletion from zero Coverage.py execution alone.
- No deletion from absent Grimp import chains alone.
- No renaming solely because a filename contains `phase`, `part`, `fix`, or `hardening`.
- No change to persisted state/schema without migration and rollback.
- No validation PR may send live Telegram messages or mutate production state.
- Every structural change gets focused tests, normal project validation, and post-merge production verification.
