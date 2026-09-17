# Stage C module migration history

This file records compatibility-preserving Stage C module migrations and retirements. The machine-readable source of truth is `config/module_migrations.json`; detailed retirement proof lives under `docs/research/`.

## `app.live_recovery_hardening` → `app.x_degraded_recovery_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical runtime owns degraded-X provider fallback, degraded-source reconciliation, recovery classification, and the final scheduled-scan integration. Production has imported and installed `app.x_degraded_recovery_runtime` directly from `app.sentry_runtime` since PR #77.

Retirement does not change authenticated-X cursor authority, syndication-first recovery, bounded FxTwitter fallback, degraded-source accounting, recovery classification, held-cursor behavior, the `_hani_live_recovery_hardening` idempotency marker, state/database schemas, Telegram delivery, schedules, secrets, or production dependencies.

The final pre-removal Maintenance plan on production `main` found three legacy references: one compatibility-test import and two dynamic compatibility/registry strings, with zero production importers, zero import-order-sensitive legacy references, and zero parse errors. The focused recovery tests continue to cover fallback ordering, outcome classification, degraded-batch reconciliation, and idempotent scheduled-scan installation through the canonical module.

Full evidence is recorded in `docs/research/live-recovery-hardening-shim-retirement-2026-09-17.md`. Maintenance no longer generates a recurring plan for this retired migration.

## `app.phase2_correlation_stability` → `app.lifecycle_correlation_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical runtime preserves first lifecycle event and translation-job identity across later grouping labels and retries. PR #90 retired the legacy path after fresh LibCST/Grimp/Coverage evidence and a full production proof. Details: `docs/research/phase2-correlation-stability-shim-retirement-2026-09-17.md`.

## `app.phase2_final_visibility` → `app.lifecycle_outcome_visibility_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical runtime exposes partial-media and fidelity-rejection outcomes without changing delivery semantics. PR #89 retired the legacy path after compatibility-only references were isolated and production verification passed. Details: `docs/research/phase2-final-visibility-shim-retirement-2026-09-17.md`.

## `app.channel_part4_finalfix` → `app.channel_source_fact_normalization_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical runtime owns source-authorized term normalization and bounded English ordinal handling. PR #87 retired the historical path after the remaining references were proven to be maintenance fixtures/registry evidence and production verification passed. Details: `docs/research/channel-part4-finalfix-shim-retirement-2026-09-17.md`.

## Post-closure staged migrations

The four original registered migrations are retired and production-proven. PR #95 then completed the closure audit of the remaining historical implementation modules. PR #97 completed the required focused coverage precursor for the next approved candidate, `app.channel_part4_humanfix`.

## `app.channel_part4_humanfix` → `app.channel_human_quality_gate_runtime`

Status: **compatibility shim active**  
Introduced: **2026-09-18**

The canonical runtime owns the established human-quality-gate responsibility: source-fidelity checks, speaker/metadata repair, optional human-polish generation and acceptance, writer patching, benchmark resume invalidation, and the human-gate fingerprint contract.

The historical path remains a same-module-object compatibility alias. This is required because the downstream quality-repair layer intentionally mutates `HUMAN_GATE_VERSION`, `HUMAN_GATE_FINGERPRINT`, and `verify_hard_facts`; two Python module objects could otherwise diverge.

The planning-first Maintenance run found eight structural references, zero dynamic/manual references, one import-order-sensitive reference (`app/__init__.py`), and zero parse errors. The coherent importer family is `app/__init__.py`, `channel_part4_qualityfix`, `channel_part4_benchmark_hook`, the human/quality/freshness tests, and the human benchmark tool. All known callers migrate to the canonical path while preserving their local binding names.

Behavior and install order are unchanged. The canonical runtime remains after `channel_part4_hardening` and `channel_source_fact_normalization_runtime`, and before `channel_part4_qualityfix` and `channel_part4_benchmark_hook`.

The legacy shim must not be removed in this migration. Removal is a later focused PR after every gate in `config/module_migrations.json` passes and production behavior is independently verified. `channel_part4_qualityfix` remains the next deferred migration candidate only after this human-quality-gate path is stable.

Stage D architecture-boundary enforcement remains deferred until the remaining explicitly approved Stage C migrations are complete or intentionally deferred.
