# Stage D resumable-recovery boundary promotion — 2026-09-19

## Decision

Promote the existing `stage-d-x-resumable-recovery-owner` Import Linter contract and its companion literal dynamic-import audit from report-only observation to blocking Maintenance enforcement.

Do not add a third architecture contract in this change.

## Protected boundary

Protected module:

`app.x_resumable_recovery_runtime`

Exact allowed direct importers:
- `app`;
- `app.x_recovery_integrity_runtime`;
- `app.completeness_provider_proof`.

`as_packages=False` remains mandatory so descendants of those allowed modules are not implicitly authorized.

This is a same-module-object ownership contract:
- package startup controls install order;
- recovery integrity patches `_update_dicts`, `_sanitize_checkpoint`, and `_lookup_user`;
- completeness provider proof replaces `_provider_page`.

A new direct importer therefore represents an architectural change and should require explicit review instead of silently broadening coupling.

## Upstream semantics revalidated

Before the pilot and again before promotion, official Import Linter and Grimp documentation were rechecked.

Import Linter Protected contracts prevent direct imports except from an allow-list. `as_packages=False` treats configured expressions as exact modules rather than packages.

Grimp defines a direct import as an import from one module to another and exposes `find_modules_that_directly_import(module)` for this relationship.

References:
- https://import-linter.readthedocs.io/en/stable/contract_types/protected/
- https://grimp.readthedocs.io/en/stable/usage.html

## Pilot PR evidence

PR #117 head:

`219d12a432fbd2475f9eaf7ecfc9ee30a959ee72`

Maintenance #152:
- artifact id: `10566760225`;
- digest: `sha256:31cdc5550146b3e3029c349a639dbebd86030917e2ded3eecdf8a4510e0a56f8`;
- existing blocking recovery-integrity contract: 1 kept / 0 broken / blocking exit 0;
- resumable-recovery candidate contract: 1 kept / 0 broken / report-only exit 0;
- integrity literal dynamic audit: 249 Python files / 0 violations / 0 parse errors;
- resumable-recovery literal dynamic audit: 249 Python files / 0 violations / 0 parse errors / report-only exit 0;
- full PR coverage/evidence collection green.

Other PR-head checks:
- Workflow Safety #29 — green;
- Security #160 — green;
- Daily #4238 — green;
- Translation Benchmark #453 — green;
- Fanfic #1002 — green.

PR #117 changed maintenance/config/docs/tests only and merged as:

`d4220ed1528bb7b4ae8cb410f3d9127bb9dc4567`

## Real-main observation evidence

Maintenance #153 on the merge SHA:
- artifact id: `10566932928`;
- digest: `sha256:8dd35fe8b359cd17d3a0b1927805b79d63fca510330806f65ee99d3624ef4f53`;
- resumable-recovery candidate: 1 kept / 0 broken / report-only exit 0;
- resumable-recovery literal dynamic audit: 249 files / 0 violations / 0 parse errors;
- recovery-integrity blocking contract remained 1 kept / 0 broken / blocking exit 0;
- recovery-integrity literal dynamic audit remained 0 violations / 0 parse errors.

Other real-main proof:
- Security #161 — green;
- Workflow Safety #30 — green;
- Fanfic #1003 — green;
- Daily #4239 — green:
  - runtime smoke passed;
  - live-provider checks passed;
  - one complete automatic monitor pass passed;
  - private-review DB checkpoint passed;
  - production outcome upload passed;
  - bot-state save passed;
  - private-review DB save passed;
- independent Watchdog #3344 — green.

## Coverage interpretation

Focused recovery coverage remains approximately:
- `app.x_resumable_recovery_runtime.py`: 77% rounded;
- `app.x_recovery_integrity_runtime.py`: 99% rounded.

The base module's lower gross coverage is documented in prior recovery coverage research: several implementation bodies are intentionally superseded at package startup by the integrity and provider-proof layers.

Promotion protects **who may own/directly import the canonical module**, not a claim that every historical implementation branch is equally active.

No coverage threshold is added.

## Non-Python entrypoint review

Before the report-only pilot, current workflow/Docker/CLI/config entrypoints were inspected for `x_resumable_recovery_runtime`.

The only non-Python reference was the Maintenance coverage target itself. No production command, Docker entrypoint, workflow command, or CLI path dynamically imported the protected module.

No new non-Python entrypoint was introduced by PR #117.

## Promotion change

Maintenance CI changes only enforcement mode:

1. Existing recovery-integrity Import Linter contract remains blocking.
2. Existing recovery-integrity literal dynamic-import audit remains blocking.
3. Resumable-recovery Import Linter contract becomes blocking.
4. Resumable-recovery literal dynamic-import audit becomes blocking.
5. Each Import Linter contract remains explicitly selected with `--contract`.
6. Both run with `--no-cache`.
7. Neither contract has `ignore_imports`.
8. Config tests lock both exact allow-lists and the blocking workflow steps.

The shared `.importlinter` contract definitions do not broaden.

## Failure policy

If the new blocking contract fails in the future:
- do not automatically add the importer to the allow-list;
- do not automatically rewrite imports;
- determine whether the new dependency represents an intentional recovery ownership change;
- preserve import/install order and persisted recovery semantics;
- require focused tests and production validation for any intentional architecture change.

If the dynamic bypass audit fails:
- do not suppress the finding with an ignore rule;
- inspect the literal loader/sys.modules path directly;
- prefer stable runtime interfaces over hidden dynamic coupling.

## Promotion merge gate

Merge only if:
- both Import Linter contracts are kept with blocking exit 0;
- both literal dynamic-import audits report 0 violations and 0 parse errors;
- focused architecture config/workflow tests pass;
- Maintenance, Security, Workflow Safety, Daily and all other triggered checks are green;
- diff contains no runtime/app implementation changes;
- production dependencies/state/provider/delivery/schedule/secret behavior is unchanged.

After merge:
- require real-main Maintenance to repeat both blocking results;
- require normal production proof and independent Watchdog;
- do not add a third architecture candidate in the same promotion cycle.

## Protected invariants

Promotion must not change:
- `x_retrieval_checkpoints`;
- `CHECKPOINT_VERSION`;
- checkpoint identity/normalization;
- retries/fallback accounting;
- source authorization;
- provider pagination/cursor advancement;
- recovery import/install order;
- Telegram/AO3 delivery;
- state/database schemas;
- schedules or secrets;
- production dependencies.
