# Active module migrations

This file records compatibility-preserving Stage C module migrations. The machine-readable source of truth is `config/module_migrations.json`.

## `app.live_recovery_hardening` → `app.x_degraded_recovery_runtime`

Status: **compatibility shim active**  
Introduced: **2026-09-16**

The canonical implementation now has a responsibility-based name: it integrates the degraded X recovery providers with production outcome accounting and the final scheduled-scan runtime.

The old path is intentionally retained. It resolves to the exact same Python module object as `app.x_degraded_recovery_runtime` rather than copying or re-exporting bindings into a second module namespace. This matters because the runtime owns mutable idempotency flags such as `_PROVIDER_INSTALLED` and `_CLASSIFICATION_INSTALLED`; two module objects could diverge and install the same patches twice.

The production entrypoint imports the canonical path directly. The compatibility path exists only to keep older imports safe while callers migrate.

### Behavior contract

This migration does not change:
- authenticated X cursor authority;
- syndication-first degraded recovery;
- bounded FxTwitter fallback behavior;
- degraded-source attempt/failure tracking;
- outcome classification or cursor-hold recovery semantics;
- scheduled-scan installation order;
- persisted state or database schemas;
- Telegram delivery, schedules, secrets, or production dependencies.

The implementation marker `_hani_live_recovery_hardening` is deliberately retained during the migration so idempotency behavior does not change merely because the source module was renamed.

### Removal policy

Do not remove `app/live_recovery_hardening.py` in the initial migration. Removal requires every gate in `config/module_migrations.json` to pass and must happen in a later focused pull request with a fresh LibCST plan, focused tests, full CI, and post-merge production verification.

### Evidence used for the first migration

Stage B measured one direct runtime importer (`app.sentry_runtime`) and the production chain `app.__main__ → app.sentry_runtime → app.live_recovery_hardening`. Focused recovery tests cover fallback ordering, outcome classification, degraded-batch reconciliation, and idempotent installation. The Stage C compatibility test additionally requires the legacy and canonical import paths to resolve to the same module object.

## `app.phase2_correlation_stability` → `app.lifecycle_correlation_runtime`

Status: **compatibility shim active**  
Introduced: **2026-09-16**

The canonical implementation now uses the stable responsibility name `lifecycle_correlation_runtime`: it freezes an update's first lifecycle event identifier and translation job identifier so later grouping labels, retries, and delivery stages cannot create a new logical correlation identity.

The old Phase 2 path is intentionally retained as a same-module-object compatibility alias. Production package initialization imports `app.lifecycle_correlation_runtime` directly at the exact position formerly occupied by `app.phase2_correlation_stability`.

### Behavior contract

This migration does not change:
- the update-based stable translation-job ID algorithm;
- first-event-ID preservation across later lifecycle writes;
- retry status or lifecycle state updates;
- the patched `StateStore.record_update_state` binding;
- the patched `zero_silent_miss.translation_job_id` binding;
- the `_phase2_correlation_stable` idempotency marker;
- state or database schemas;
- provider collection, Telegram delivery, schedules, secrets, or production dependencies.

Import order is part of the contract. The canonical lifecycle-correlation runtime remains after `zero_silent_miss`, `phase2_runtime_compat`, and `phase2_final_visibility`, and before Event Fusion.

### Removal policy

Do not remove `app/phase2_correlation_stability.py` in this migration. Removal requires every gate in `config/module_migrations.json` to pass and must happen in a later focused pull request after a fresh LibCST plan shows no unresolved runtime caller.

### Evidence used for this migration

Fresh Stage B import-graph evidence measured one direct importer (`app`) and one downstream importer. The most recent PR run with per-test Coverage.py evidence measured approximately 93.5% execution coverage across 32 test contexts for the module, stronger than the other small Phase 2 rename candidates. The dedicated `tests/test_phase2_correlation.py` regression test verifies that event and translation correlation IDs remain stable across stage-label changes and retries. Repository history also shows that the module was introduced specifically to freeze logical update correlation IDs, so the semantic target name describes an established responsibility rather than inventing a new abstraction.
