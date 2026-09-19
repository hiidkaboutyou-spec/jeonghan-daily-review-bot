# Stage D Fanfic / AO3 Isolation Pilot — 2026-09-19

## Status

Report-only Stage D architecture candidate.

Base main before this pilot:

`21a3502c6455c5b2c2829c305397b1b83fe33b45`

This base already contains two production-proven blocking recovery-family architecture contracts:

1. `app.x_recovery_integrity_runtime` direct-import ownership;
2. `app.x_resumable_recovery_runtime` direct-import ownership.

PR #118 promoted the second contract to blocking and was independently proven on real main by Maintenance #155, Security #164, Workflow Safety #32, Nightly Fanfic #1006, Daily #4247, and Watchdog #3356.

This pilot does **not** weaken, replace, or broaden either blocking recovery contract.

## Architectural question

Should the production Fanfic/AO3 digest remain isolated from the Daily-only Translation/Style/Calibration/Forward-ready/Fused-delivery shadow stack?

Current runtime intent says yes.

`app/__init__.py` explicitly documents that Translation Fusion is lazy-loaded inside the Daily-only private-review hook so importing/running `app.fic_digest` does not acquire a non-Fanfic Translation Fusion runtime dependency.

`app.event_fusion_private_runtime.ensure_translation_fusion_shadow()` lazy-loads the Daily-only stack only when private-review delivery reaches that path.

The production Fanfic workflow executes:

`python -m app.fic_digest`

The Fanfic module directly depends on shared primitives such as configuration, AI provider helpers, Telegram, X lookup, structured Gemini output, message delivery, and Fanfic-specific state/quality code. Those shared primitives are intentionally **not** forbidden.

## Candidate forbidden set

Exact source module:

- `app.fic_digest`

Exact Daily-only modules forbidden from the Fanfic source dependency graph:

- `app.translation_fusion`
- `app.translation_fusion_runtime`
- `app.translation_fusion_state_compat`
- `app.channel_style_rewrite`
- `app.channel_style_rewrite_state_compat`
- `app.user_voice_calibration`
- `app.user_voice_calibration_state_compat`
- `app.forward_ready_package`
- `app.forward_ready_state_compat`
- `app.fused_private_review_delivery`

The candidate uses a Forbidden Import Linter contract with `as_packages=False`.

No `ignore_imports`.

No `allow_indirect_imports=True`.

Therefore both direct and indirect chains from the exact `app.fic_digest` module to any exact forbidden module are evidence.

## Explicit non-forbidden modules

Do not forbid:

- `app.event_fusion_private_runtime`
- `app.ai`
- `app.config`
- `app.telegram`
- `app.x_client`
- `app.message_delivery`
- `app.gemini_structured`
- Fanfic state/summary-quality modules.

`app.event_fusion_private_runtime` is intentionally imported by package initialization today. The relevant invariant is that its Daily-only stack remains lazy and is not acquired by the Fanfic execution path.

## Upstream semantics reviewed

### Import Linter 2.15

Official documentation confirms Forbidden contracts:

- prevent listed source modules from importing listed forbidden modules;
- inspect indirect import chains by default;
- support exact-module semantics through `as_packages=False`;
- may opt out of indirect checking with `allow_indirect_imports=True`, which this pilot intentionally does not set;
- support `broken_contract_guidance`.

Release 2.15 was published 2026-09-04. No new maintenance dependency is needed because Hani already pins Import Linter 2.15.

References:

- https://import-linter.readthedocs.io/en/latest/contract_types/forbidden/
- https://import-linter.readthedocs.io/en/latest/release_notes/

### Grimp 3.17

Official Grimp documentation distinguishes:

- direct imports;
- import chains;
- `find_shortest_chain`;
- `chain_exists`;
- exact-module versus package semantics.

This matches the graph evidence model used by the pilot.

Reference:

- https://grimp.readthedocs.io/en/stable/usage.html

### Python package initialization

Python's regular-package import semantics execute the parent package's `__init__.py` when a package/submodule is imported. That matters here because a static graph from `app.fic_digest` alone does not model package-initialization side effects as if `app.fic_digest` directly imported every module loaded by `app/__init__.py`.

Therefore the static Forbidden contract is necessary but not sufficient.

Reference:

- https://docs.python.org/3.12/reference/import.html

## Companion evidence

### 1. Source-scoped literal dynamic-import audit

`tools/fanfic_import_isolation_audit.py` reuses the already-proven AST logic from `tools/protected_import_bypass_audit.py`, but scans **only** `app.fic_digest` against the Fanfic forbidden set.

This avoids globally banning those Daily-only modules, because they are legitimate dependencies elsewhere in the Daily runtime.

It catches literal use of common dynamic-loading APIs, literal import code passed to exec/eval/compile, and literal `sys.modules` access through the existing protected-import scanner.

Computed/non-literal runtime names still require code review.

### 2. Clean-process package-initialization probe

The same audit starts a fresh Python subprocess and imports `app.fic_digest`.

It records whether:

- `app.fic_digest` actually loaded;
- `app.event_fusion_private_runtime` loaded as the package-initialization witness;
- any forbidden Daily-only module appeared in `sys.modules`.

The package-init witness is important: a probe that did not execute the expected package initializer would be invalid evidence.

This runtime probe performs import-only validation. It does not run AO3 retrieval, Gemini calls, Telegram delivery, X collection, schedules, or manuscript/fanfic content processing.

## Why report-only first

This is the third Stage D architecture candidate and the first one protecting a product/runtime boundary outside the recovery family.

Even though the current evidence is strong, the project rule remains:

1. observe the candidate first;
2. prove PR-head Maintenance + Fanfic + Security;
3. merge the report-only pilot only after an exact-head landing decision;
4. require independent real-main Maintenance plus Nightly Fanfic and normal Daily/Watchdog proof;
5. only then consider a separate blocking-promotion PR.

A broken candidate report is evidence to investigate, not permission to broaden the forbidden set, add ignore rules, or rewrite runtime code automatically.

## Pilot implementation

### Static contract

Contract ID:

`stage-d-fanfic-daily-shadow-isolation`

Properties:

- type: `forbidden`
- source: exact `app.fic_digest`
- forbidden set: exact ten Daily-only modules
- `as_packages=False`
- no `ignore_imports`
- indirect imports remain checked
- explicit `broken_contract_guidance`
- selected separately with `--contract`
- `--no-cache`
- report-only exit status

### Runtime/dynamic companion

`tools/fanfic_import_isolation_audit.py`:

- source-scoped literal dynamic-import scan;
- clean-process package-init probe;
- JSON + Markdown evidence;
- no source mutation;
- candidate result is report-only.

`tests/test_fanfic_import_isolation_audit.py` tests the audit mechanism itself and is blocking. Those tests use synthetic temporary packages and do not make the candidate architecture result blocking.

## Protected invariants

This pilot must not change:

- `app.fic_digest` implementation;
- AO3 retrieval/ranking/summarization behavior;
- Fanfic spoiler modes;
- Telegram targets/delivery;
- shared private-review database behavior;
- X collection or configured sources;
- provider/model defaults;
- production secrets;
- schedules;
- recovery checkpoint semantics;
- either existing blocking recovery architecture contract;
- production dependencies/Docker image.

## Promotion gate

Do not promote this Fanfic candidate in the same PR.

A later promotion requires all of:

1. PR-head candidate Forbidden contract: 1 kept / 0 broken / report-only exit 0;
2. PR-head Fanfic companion audit: 0 literal violations, 0 runtime-loaded forbidden modules, 0 probe/parse errors;
3. existing two recovery contracts and dynamic audits remain blocking/green;
4. Nightly Fanfic PR validation succeeds;
5. Security and Workflow Safety succeed;
6. report-only pilot lands without runtime behavior changes;
7. real-main Maintenance repeats the clean candidate result;
8. real-main Nightly Fanfic succeeds;
9. normal Daily production pass succeeds;
10. independent Watchdog succeeds;
11. no new legitimate Fanfic feature requires the forbidden Daily-only stack;
12. promotion occurs in a separate focused PR.

## Deferred / rejected alternatives

- Do not add Tach in parallel.
- Do not convert `app.source_authority_hardening` into a protected allow-list; future source integrations may legitimately need it.
- Do not turn `app.phase2_runtime_compat` into a permanent contract.
- Do not add a generic rule for every one-importer module.
- Do not forbid shared primitives used by both Daily and Fanfic.
- Do not refactor package initialization in this pilot.
- Do not move Translation Fusion modules merely to make the contract easier to express.

## Durable continuation

If this branch/PR is interrupted, continue in this order:

1. verify the exact current pilot head;
2. inspect Maintenance candidate static + runtime/dynamic reports;
3. verify the existing recovery blocking gates remain unchanged;
4. verify Nightly Fanfic, Security, Workflow Safety, and normal project tests;
5. do not promote in the pilot PR;
6. after a report-only merge, inspect real-main evidence before creating any promotion or fourth candidate.
