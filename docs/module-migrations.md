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
