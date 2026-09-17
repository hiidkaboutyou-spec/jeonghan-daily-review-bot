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

## Registered-shim subphase

All four migrations registered in `config/module_migrations.json` are now marked `retired`; no registered `compatibility-shim` path remains. The canonical modules remain production-authoritative and their behavior/import order is unchanged.

The next Stage C action is a **closure audit**, not another automatic rename. Run fresh Maintenance with per-test Coverage contexts and inspect the remaining historical-name implementation modules. Each must be explicitly classified as either:

- **migrate later** — a semantic boundary and adequate direct regression evidence justify a staged migration; or
- **retain by design** — the current path is an intentional production contract or renaming would add risk without useful architectural value.

Stage D architecture-boundary enforcement remains deferred until that closure audit establishes stable intended boundaries.
