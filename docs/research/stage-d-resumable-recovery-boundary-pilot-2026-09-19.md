# Stage D resumable-recovery boundary pilot — 2026-09-19

## Decision

Introduce exactly one **second architecture candidate** in report-only mode:

`app.x_resumable_recovery_runtime`

The existing recovery-integrity contract stays the only blocking Import Linter contract.

The candidate protected contract allows exactly these direct application importers:
- `app` — package startup owns install order;
- `app.x_recovery_integrity_runtime` — deliberately patches recovery checkpoint/profile helpers on the canonical module object;
- `app.completeness_provider_proof` — deliberately replaces the provider-page proof implementation on the same canonical module object.

`as_packages=False` is required so descendants of those modules are not implicitly authorized.

## Why this candidate

Fresh real-main Maintenance #151 on `c325e388bbf557ee1959442410970c4ad3dfcd01` reports the current direct importer set for `app.x_resumable_recovery_runtime` as exactly:
- `app`;
- `app.completeness_provider_proof`;
- `app.x_recovery_integrity_runtime`.

That set matches the recovery family's durable design documentation rather than an accidental low-degree graph node.

Historical recovery planning and migration evidence records the same ownership contract:
- the base recovery module owns resumable X pagination/checkpoints/retries;
- the integrity layer mutates `_update_dicts`, `_sanitize_checkpoint`, and `_lookup_user` on the canonical base module object;
- completeness provider proof mutates `_provider_page` on that same canonical module object;
- package startup must preserve the relative recovery installation order.

Current `app/__init__.py` still states that base recovery remains responsible for provider pagination/checkpoints/retries and imports integrity immediately after it.

Current source inspection confirms:
- `app.x_recovery_integrity_runtime` imports the canonical base module and patches its helper names at call-time;
- `app.completeness_provider_proof` imports the canonical base module and replaces `_provider_page` during install.

This makes the direct-import allow-list architectural ownership, not merely the current graph shape.

## Candidate comparison

### Selected: `app.x_resumable_recovery_runtime`

Strengths:
- semantic canonical module, not a historical compatibility path;
- exactly three current direct importers;
- all three importers are documented owners of import/install behavior;
- prior Stage C migration explicitly required all mutation layers to converge on this same module object;
- recovery coverage precursor and later PR evidence directly protect checkpoint/state/retry/fallback behavior;
- production has repeatedly proven the canonical recovery family after migration and architecture enforcement.

Caution:
- focused PR coverage is about 77%, because several original implementation bodies are intentionally superseded at package startup;
- therefore the second contract must begin report-only and must not be treated as proof that recovery code is simple or immutable.

### Rejected for this pilot: `app.source_authority_hardening`

Current direct importers are `app`, `app.x_resumable_recovery_runtime`, and `app.zero_silent_miss`.

It remains an important retain-by-design policy layer, but a future source/retrieval implementation may legitimately need source-authority access. Freezing today's importer set would risk constraining normal source expansion rather than protecting a deliberately closed ownership boundary.

### Rejected for this pilot: `app.phase2_runtime_compat`

It currently has only `app` as a direct importer, but it is explicitly a compatibility boundary with a historical name. Turning that incidental single-importer shape into a new enforced architecture contract would cement compatibility structure rather than clarify the intended long-term architecture.

### Rejected for this pilot: package-startup-only runtime installers

Several runtime installers currently have only `app` as a direct importer. Low importer count alone is insufficient. Stage D should protect semantic ownership boundaries with explicit reasons, not generate contracts mechanically from graph degree.

## Current static evidence

Main Maintenance #151 inventory:
- current app/tool Python inventory is parse-clean;
- `app.x_resumable_recovery_runtime` current direct importers are the three deliberate owners above.

Focused recovery coverage from PR Maintenance #150:
- `app.x_resumable_recovery_runtime.py`: 469 statements, 77% rounded coverage;
- `app.x_recovery_integrity_runtime.py`: 99% rounded coverage.

The base recovery misses are not automatically a blocker for a report-only ownership contract because existing recovery research documents that large missed regions are superseded implementation bodies. They are a reason **not** to promote this candidate immediately.

## Non-Python entrypoint sweep

Before introducing the pilot, 12 current workflow/Docker/CLI/config entrypoint files were scanned for `x_resumable_recovery_runtime`.

Only one reference exists:
- Maintenance coverage explicitly names `app/x_resumable_recovery_runtime.py`.

No production command, Docker entrypoint, workflow command, or CLI string dynamically imports the candidate module.

## Upstream contract semantics

Official Import Linter documentation confirms:
- protected contracts restrict **direct imports** to an allow-list;
- `as_packages=False` treats configured expressions as exact modules rather than whole packages;
- `lint-imports --contract <id>` can evaluate individual contracts independently.

Official Grimp documentation confirms that `find_modules_that_directly_import(module)` represents direct static importer relationships.

References:
- https://import-linter.readthedocs.io/en/stable/contract_types/protected/
- https://import-linter.readthedocs.io/en/latest/get_started/run/
- https://grimp.readthedocs.io/en/stable/usage.html

## Pilot design

The shared `.importlinter` config now contains two protected contracts, but CI treats them differently.

### Blocking contract

`stage-d-x-recovery-integrity-owner`

The existing recovery-integrity contract remains blocking and is selected explicitly with `--contract`.

### Report-only candidate

`stage-d-x-resumable-recovery-owner`

Allowed exact importers:
- `app`;
- `app.completeness_provider_proof`;
- `app.x_recovery_integrity_runtime`.

CI runs this contract separately with `--contract`, captures its exit status in `import-linter-resumable-report.txt`, and does not fail the build from the candidate result.

The existing AST bypass tool is also reused for the candidate module in report-only mode. Its JSON/Markdown report captures literal dynamic-loading/sys.modules bypass evidence without changing runtime behavior.

The blocking recovery-integrity static/dynamic gates remain unchanged.

## Pilot acceptance gate

This pilot may merge only if:
- the original recovery-integrity blocking contract remains kept;
- its blocking dynamic bypass audit remains clean;
- the new candidate's report is inspectable and technically valid;
- candidate dynamic-bypass output is inspectable;
- config-scope tests prove there are exactly two contracts and zero ignore rules;
- Security, Workflow Safety, Daily, Render, CodeQL and other triggered checks remain green;
- production requirements/runtime/state/provider/delivery files remain unchanged.

The candidate does **not** need to be promoted in this PR. If either candidate report shows unexpected coupling, record the coupling and keep it report-only or remove the candidate rather than rewriting production solely to make the contract green.

## Promotion gate

Do not make `stage-d-x-resumable-recovery-owner` blocking until a later focused PR after:
1. the PR-head candidate contract is kept with zero broken contracts;
2. the candidate dynamic bypass audit is clean;
3. a real-main Maintenance run repeats those results;
4. current direct importer evidence still matches the documented three-owner model;
5. normal real-main production validation remains green;
6. no ignore rule is required.

## Protected invariants

This pilot must not change:
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
