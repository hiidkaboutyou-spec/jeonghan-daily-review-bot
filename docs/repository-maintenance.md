# Repository maintenance and cleanup

This document defines the safe cleanup strategy for Daily Hani. The goal is to make the repository easier to understand and maintain without changing production behavior just to make filenames or folders look cleaner.

## Why cleanup must be incremental

The application currently has deliberate import-time installation layers in `app/__init__.py`. Some modules with historical names such as `phase*`, `part*`, `*fix`, or `*hardening` are still active runtime components. A filename that looks temporary is therefore only a cleanup candidate, not evidence that the file is unused.

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

Rationale: Ruff is actively maintained, fast, and combines several mature lint/import/format checks. The conservative gate avoids turning historical style debt into an unsafe mass rewrite.

### Vulture 2.16 — adopted, report only

Repository: `jendrikseipp/vulture`
Audited release commit: `b0f67ba0044693aa9ec0d38fe460590facc98004`
License: MIT

Use now:
- report dead-code candidates at confidence >= 90;
- never delete a symbol or module solely because Vulture reports it.

Rationale: Hani uses import-time patch/install layers and dynamic behavior, so static dead-code analysis can produce false positives. Every candidate needs direct reference inspection plus tests before removal.

### Deptry 0.25.1 — adopted, report only

Repository: `osprey-oss/deptry`
Audited release commit: `0c39226d761685125c5ff71ca81282d6751c9540`
License: MIT

Use now:
- compare imports under the repository with `requirements.txt`;
- surface possibly unused, missing, or transitive dependencies;
- never remove a dependency until optional/dynamic imports, workflows, Docker, and production smoke are checked.

Known Hani static-analysis exceptions are documented for Agent Reach, `twitter-cli`, Uvicorn, and the direct `daily_watchdog_hardening.py` entry point. After those intentional entry points were modeled, the real repository scan reported no dependency issues.

### Complexipy 8.0.1 — adopted, report only

Repository: `rohaquinlop/complexipy`
Audited release commit: `030e2079457412221087f520445e9f2a709faad6`
License: MIT

Use now:
- rank cognitively complex functions in `app/`;
- keep the report non-blocking;
- use scores to choose where focused refactoring could improve maintainability;
- never apply generated refactor suggestions automatically.

Rationale: cognitive complexity adds a signal Ruff, Vulture, and Deptry do not provide. The first real Hani run identified concrete hotspots such as `CaptionWriter.write_group` and several historical channel-hardening functions. The permanent report intentionally omits verbose generated rewrite suggestions.

### Coverage.py 7.16.1 — adopted for Stage B, CI/dev only

Repository: `coveragepy/coveragepy`
Audited release commit: `ccbb99245dcb4e51a35087285499cdb22b164ccc`
Release tag signature: verified by GitHub
License: Apache-2.0

Use now:
- measure the existing `unittest` suite without introducing pytest;
- enable `dynamic_context = test_function` so individual test functions are recorded as execution contexts;
- generate temporary JSON with line-to-test context evidence for `app/`;
- feed only the concise evidence summary into the maintenance artifact;
- do not set a global coverage percentage gate during structural cleanup.

Rationale: Stage B needs evidence that a historical module is actually executed by tests, not just that a similarly named test file exists. Zero coverage remains only evidence for investigation because scheduled, subprocess, import-time, or live-provider paths may not be exercised by the unit suite.

### Grimp 3.17 — adopted for Stage B, report only

Repository: `python-grimp/grimp`
Audited release commit: `286f0f5de79d29dea44cfa6563802e9b4fe37dea`
License: BSD-2-Clause

Use now:
- build a queryable import graph for the `app` package;
- exclude imports that exist only under `TYPE_CHECKING` from runtime evidence;
- inspect direct importers, direct dependencies, transitive downstream/upstream modules, and shortest chains from `app` / `app.__main__`;
- disable persistent Grimp caching in CI;
- never infer that a module is dead solely because the static graph has no path to it.

Rationale: the native AST inventory is intentionally simple and excellent for Stage A mapping. Grimp adds the missing Stage B signal: transitive impact and concrete import chains, without imposing architecture contracts or changing runtime behavior.

### Import Linter 2.15 — reference only for now

Repository: `seddonym/import-linter`
Reviewed commit: `31927f1457e3df673912cb5efb0afa6dbc37585f`
License: BSD-2-Clause

Decision: do not install yet.

It is useful for enforcing architectural boundaries after packages have stable responsibilities, but Hani is still a mostly flat package with intentional import-time composition. Adding architecture contracts now would encode the current transitional shape or generate noise. Reconsider after the first safe module-family consolidations.

### Tach 0.35.1 — reference only for later architecture enforcement

Repository: `tach-org/tach`
Reviewed release commit: `65df67ac51a8d0e8f9e0398ea72c924fea34fd25`
License: MIT

Tach can visualize/enforce module dependencies, public interfaces, and cycles with no production runtime impact. It is actively maintained, but its value depends on having intentional module boundaries. Installing it now would require defining boundaries for a codebase that is still being mapped, so it is deferred to Stage D rather than used to freeze accidental current coupling.

### Pydeps 3.0.8 — reference only

Repository: `thebjorn/pydeps`
Reviewed release commit: `6f73953ef47e6ff40c1fdfaf692c3d0bc47e3867`
License: BSD-2-Clause

Pydeps can visualize Python import graphs and cycles. We are not installing it now because Hani's native AST inventory plus Grimp provide the import-edge and transitive-chain evidence required for Stage B without adding Graphviz/display tooling or a second visualization pipeline. Reconsider it only if visual cycle analysis becomes materially useful.

### Refurb 2.3.1 — rejected for the current cleanup stage

Repository: `dosisod/refurb`
Reviewed release commit: `0dbb127465ca9398b6c89c32a7fd86d78ca755c4`
License: GPL-3.0

Refurb focuses on modernizing/refactoring suggestions and relies on type-analysis behavior. This substantially overlaps current lint/refactor signals while creating another false-positive surface in a dynamic codebase. It is not installed. Focused human-reviewed refactors driven by tests plus Ruff/Complexipy are safer here.

## Native structure and Stage B evidence

`tools/repo_structure_inventory.py` provides a mutation-free map of `app/` and `tools/`:
- module path;
- coarse responsibility category;
- internal `app`/`tools` imports;
- historical phase/fix-style names;
- parse errors.

`tools/module_family_evidence.py` adds Stage B evidence for historical-name candidates:
- direct and transitive Grimp import relationships;
- shortest static chains from `app` and `app.__main__`;
- optional per-test Coverage.py execution contexts;
- a conservative risk label and review hint.

Neither report calls a historical filename "dead". A candidate with no static importer and no measured test context is only the lowest-risk place to investigate first. Dynamic imports, subprocess entry points, workflows, scheduled jobs, and production-only paths still require direct inspection.

## CI enforcement levels

`Hani Maintenance Diagnostics` uses three levels:

1. **Blocking:** Ruff definite Python errors, inventory parse errors, and the existing unit suite when per-test Stage B coverage is collected.
2. **Report-only:** Grimp Stage B relationships, Coverage.py percentages/contexts, Ruff import ordering/format checks, Vulture high-confidence candidates, Deptry findings, and Complexipy complexity hotspots.
3. **Human/agent review:** any move, rename, deletion, dependency removal, complexity refactor, or package-boundary change.

Coverage collection runs on pull requests, scheduled runs, and manual runs. It is skipped on the immediate `main` push because the normal production validation already runs the full suite there; the static Grimp Stage B report still runs. Maintenance tooling is installed only in an ephemeral CI virtual environment from `requirements-maintenance.txt`. It is not part of the production Docker dependency graph.

## Cleanup roadmap

### Stage A — inventory and diagnostics

Completed. Hani has a stable maintenance diagnostics layer with structure inventory, definite-error linting, dead-code candidates, dependency checks, and complexity hotspots, without changing production runtime dependencies.

### Stage B — classify active module families

Current stage.

For each historical module family, establish:
- who imports it directly and indirectly;
- whether it has a static chain from an application entry point;
- whether import order matters;
- which concrete tests execute it and how much of it they execute;
- persisted-state/config compatibility;
- workflow/CLI/subprocess/dynamic-import references that static analysis cannot prove;
- whether it is runtime, compatibility-only, benchmark-only, or genuinely obsolete;
- whether complexity is local and safely reducible without changing semantics.

Start investigation with candidates that have the least static and test evidence, but never delete from absence of evidence alone.

### Stage C — consolidate one family at a time

Prefer semantic names based on responsibility rather than development history. If a production module moves, keep a compatibility shim when needed and update tests in the same PR. Never combine unrelated module-family moves into one cleanup PR.

### Stage D — enforce stable package boundaries

Only after responsibilities are actually stable, reconsider Tach, Import Linter, or equivalent architecture contracts. The tool must describe the architecture we intentionally want, not freeze accidental historical coupling.

## Non-negotiable cleanup rules

- No bulk auto-fix across production code.
- No automatic deletion from Vulture output.
- No dependency removal from Deptry output alone.
- No automatic rewrite from Complexipy or another refactoring recommender.
- No deletion from zero Coverage.py execution alone.
- No deletion from absent Grimp import chains alone.
- No renaming solely because a filename contains `phase`, `part`, `fix`, or `hardening`.
- No change to persisted state/schema without migration and rollback.
- No validation PR may send live Telegram messages or mutate production state.
- Every structural change gets focused tests, normal project validation, and post-merge production verification.
