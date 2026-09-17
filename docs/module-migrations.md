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

Import order is part of the contract. The canonical lifecycle-correlation runtime remains after `zero_silent_miss`, `phase2_runtime_compat`, and `lifecycle_outcome_visibility_runtime`, and before Event Fusion.

### Removal policy

Do not remove `app/phase2_correlation_stability.py` in this migration. Removal requires every gate in `config/module_migrations.json` to pass and must happen in a later focused pull request after a fresh LibCST plan shows no unresolved runtime caller.

### Evidence used for this migration

Fresh Stage B import-graph evidence measured one direct importer (`app`) and one downstream importer. The most recent PR run with per-test Coverage.py evidence measured approximately 93.5% execution coverage across 32 test contexts for the module, stronger than the other small Phase 2 rename candidates. The dedicated `tests/test_phase2_correlation.py` regression test verifies that event and translation correlation IDs remain stable across stage-label changes and retries. Repository history also shows that the module was introduced specifically to freeze logical update correlation IDs, so the semantic target name describes an established responsibility rather than inventing a new abstraction.

## `app.phase2_final_visibility` → `app.lifecycle_outcome_visibility_runtime`

Status: **compatibility shim active**  
Introduced: **2026-09-16**

The canonical implementation now uses the responsibility name `lifecycle_outcome_visibility_runtime`. It exposes two already-established outcomes in durable lifecycle state and observability: partial media delivery and translation fidelity rejection that requires manual review.

The old Phase 2 path remains as a same-module-object compatibility alias. Production package initialization imports `app.lifecycle_outcome_visibility_runtime` at the exact installation position formerly occupied by `app.phase2_final_visibility`. The private package binding name `_phase2_final_visibility` is intentionally retained during the compatibility phase as an extra guard against obscure callers relying on the package's existing private attribute.

### Behavior contract

This migration does not change:
- partial-media detection from `_last_media_delivery_report`;
- the `media_status="partial_failed"` lifecycle update or `media_partial_failure` reason;
- the `telegram_media_delivery_partial` observability event;
- manual-review fidelity rejection detection;
- the `translation_status="fidelity_rejected"` lifecycle update or `manual_review_required` reason;
- the `translation_fidelity_rejected` observability event;
- the `_phase2_partial_media_visible` and `_phase2_fidelity_visible` idempotency markers;
- media delivery return values or Telegram send behavior;
- state or database schemas;
- provider collection, schedules, secrets, or production dependencies.

Import order is part of the contract. The canonical outcome-visibility runtime remains after `zero_silent_miss` and `phase2_runtime_compat`, immediately before `lifecycle_correlation_runtime`, and before Event Fusion.

### Removal policy

Do not remove `app/phase2_final_visibility.py` in this migration. Removal requires every gate in `config/module_migrations.json` to pass and must happen in a later focused pull request after a fresh LibCST plan shows no unresolved caller beyond the explicit compatibility contract.

### Evidence used for this migration

The planning-first PR ran LibCST before any production source rename. The plan found exactly one structural reference, `app/__init__.py`, with no dynamic-string references, no manual-review references, and no parse errors; it correctly flagged the package initializer as import-order-sensitive. Fresh Stage B evidence measured one direct importer (`app`) and one downstream importer, with about 84.5% execution coverage across 13 test contexts. The dedicated `tests/test_phase2_outcomes.py` tests verify both partial-media lifecycle visibility and fidelity-rejection visibility. Repository history shows the module was introduced specifically to make those two outcomes explicit, so the semantic target name records an existing responsibility rather than changing architecture.

## `app.channel_part4_finalfix` → `app.channel_source_fact_normalization_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical implementation uses a responsibility-based name for the established source-authorized normalization layer. It extends the hard-fact verifier's bounded ordinal vocabulary with English `first` through `tenth`, and canonicalizes a small set of member/term spellings in ordinary prose only when the source itself authorizes that identity or term.

The historical `app.channel_part4_finalfix` module file has been retired. Production package initialization continues to import `app.channel_source_fact_normalization_runtime` at the same position after `channel_part4_hardening` and before `channel_part4_humanfix`, `channel_part4_qualityfix`, and `channel_part4_benchmark_hook`.

The package-local binding name `_channel_part4_finalfix` is intentionally left in `app/__init__.py` for this focused retirement because it points directly to the canonical module and does not recreate or import the retired module path. Renaming that private binding is not required for legacy-module retirement and can be considered separately if future evidence shows value.

### Behavior contract

This retirement does not change:
- the bounded English ordinal mapping or its numeric values;
- semantic-number verification in `channel_part4_hardening`;
- the source aliases, canonical Persian spellings, or accepted output variants;
- the rule that source authorization is required before a prose replacement;
- hashtag and @mention protection boundaries;
- the replacement regex or case-insensitive matching behavior;
- the `hardening._canonicalize_source_authorized_terms` binding installed at import time;
- writer classes, fallback translation, human-gate versions/fingerprints, provider collection, persisted state, Telegram delivery, schedules, secrets, or production dependencies.

### Retirement evidence

The original migration in PR #80 had one direct production importer, one downstream importer, and 100% measured coverage across 12 test contexts before ownership moved. The focused behavioral tests were migrated to the canonical path then and remain canonical-only.

A fresh pre-removal Maintenance run on `main` commit `4a2d6056586406620ff01cdef4cdf7bf9503a374` found eight remaining references and all eight were dynamic/manual strings. Inspection showed they are only synthetic historical-name fixtures in maintenance-tool tests plus the migration-registry assertion; there were zero structural imports, zero import-order-sensitive references, and zero parse errors. Grimp showed no direct importer, no downstream importer, and no application entrypoint chain for the shim.

Full evidence, exact run/artifact IDs, the production proof, and the post-merge validation contract are recorded in `docs/research/channel-part4-finalfix-shim-retirement-2026-09-17.md`.

Maintenance CI no longer generates a recurring LibCST plan for this retired migration. The three still-active compatibility shims continue to receive read-only plans on every maintenance run.
