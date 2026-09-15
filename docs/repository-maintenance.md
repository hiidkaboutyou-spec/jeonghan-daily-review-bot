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
- compare imports under `app/` with `requirements.txt`;
- surface possibly unused, missing, or transitive dependencies;
- never remove a dependency until optional/dynamic imports, workflows, Docker, and production smoke are checked.

Rationale: the project still uses a requirements.txt-based production environment, which Deptry supports directly.

### Import Linter 2.15 — reference only for now

Repository: `seddonym/import-linter`
Reviewed commit: `31927f1457e3df673912cb5efb0afa6dbc37585f`
License: BSD-2-Clause

Decision: do not install yet.

It is useful for enforcing architectural boundaries after packages have stable responsibilities, but Hani is still a mostly flat package with intentional import-time composition. Adding architecture contracts now would encode the current transitional shape or generate noise. Reconsider after the first safe module-family consolidations.

## Native structure inventory

`tools/repo_structure_inventory.py` provides a mutation-free map of `app/` and `tools/`:
- module path;
- coarse responsibility category;
- internal `app`/`tools` imports;
- historical phase/fix-style names;
- parse errors.

The inventory deliberately does not call a historical filename "dead". It exists to make cleanup decisions evidence-based.

## CI enforcement levels

`Hani Maintenance Diagnostics` uses three levels:

1. **Blocking:** Ruff definite Python errors and inventory parse errors.
2. **Report-only:** Ruff import ordering/format checks, Vulture high-confidence candidates, and Deptry findings.
3. **Human/agent review:** any move, rename, deletion, dependency removal, or package-boundary change.

Maintenance tooling is installed only in an ephemeral CI virtual environment from `requirements-maintenance.txt`. It is not part of the production Docker dependency graph.

## Cleanup roadmap

### Stage A — inventory and diagnostics

Current stage. Gather real reports without changing runtime structure.

### Stage B — classify active module families

For each historical module family, establish:
- who imports it;
- whether import order matters;
- tests covering its behavior;
- persisted-state/config compatibility;
- whether it is runtime, compatibility-only, benchmark-only, or genuinely obsolete.

### Stage C — consolidate one family at a time

Prefer semantic names based on responsibility rather than development history. If a production module moves, keep a compatibility shim when needed and update tests in the same PR. Never combine unrelated module-family moves into one cleanup PR.

### Stage D — enforce stable package boundaries

Only after responsibilities are actually stable, reconsider Import Linter or equivalent architecture contracts. The tool must describe the architecture we intentionally want, not freeze accidental historical coupling.

## Non-negotiable cleanup rules

- No bulk auto-fix across production code.
- No automatic deletion from Vulture output.
- No dependency removal from Deptry output alone.
- No renaming solely because a filename contains `phase`, `part`, `fix`, or `hardening`.
- No change to persisted state/schema without migration and rollback.
- No validation PR may send live Telegram messages or mutate production state.
- Every structural change gets focused tests, normal project validation, and post-merge production verification.
