# Stage C module migration history

This file records compatibility-preserving Stage C module migrations and retirements. The machine-readable source of truth is `config/module_migrations.json`; detailed retirement proof lives under `docs/research/`.

## `app.live_recovery_hardening` → `app.x_degraded_recovery_runtime`

Status: **compatibility shim active**  
Introduced: **2026-09-16**

The canonical implementation integrates degraded X recovery providers with production outcome accounting and the final scheduled-scan runtime. The legacy path still resolves to the exact same Python module object as `app.x_degraded_recovery_runtime` because the runtime owns mutable install/idempotency state such as `_PROVIDER_INSTALLED` and `_CLASSIFICATION_INSTALLED`.

### Behavior contract

This migration does not change authenticated-X cursor authority, syndication-first degraded recovery, bounded FxTwitter fallback, degraded-source attempt/failure tracking, recovery classification, cursor-hold behavior, scheduled-scan installation order, state/database schemas, Telegram delivery, schedules, secrets, or production dependencies. The `_hani_live_recovery_hardening` marker remains part of idempotent installation behavior.

### Removal policy

This is the **last active registered shim** and is intentionally retired last. Removal requires the exact gates in `config/module_migrations.json`, a fresh LibCST/manual reference audit, focused recovery tests, full CI, exact Render image validation, and a real post-merge production monitor pass.

## `app.phase2_correlation_stability` → `app.lifecycle_correlation_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical runtime freezes an update's first lifecycle event identifier and translation-job identifier so alternate grouping labels, retries, and later delivery stages cannot create a different logical identity. Production package initialization continues to import `app.lifecycle_correlation_runtime` in the same position after `zero_silent_miss`, `phase2_runtime_compat`, and `lifecycle_outcome_visibility_runtime`, before Event Fusion.

### Behavior contract

Retirement does not change:
- the update-based stable translation-job ID algorithm;
- first-event-ID preservation across later lifecycle writes;
- retry status or lifecycle state updates;
- the patched `StateStore.record_update_state` binding;
- the patched `zero_silent_miss.translation_job_id` binding;
- the `_phase2_correlation_stable` idempotency marker;
- state/database schemas, provider collection, Telegram delivery, schedules, secrets, or production dependencies.

### Retirement evidence

The original PR #78 migration measured approximately 93.5% execution coverage across 32 test contexts and preserved the exact install position. The focused behavior test continues to verify stable event and translation correlation across stage-label changes and retries.

A fresh pre-removal plan from PR #89's final Maintenance artifact (`10505881127`) found exactly three remaining references: one legacy structural import plus two dynamic strings, all confined to compatibility assertions/registry data. There were zero production importers, zero import-order-sensitive legacy references, and zero parse errors; Grimp reported no direct/downstream importer or application entrypoint chain for the shim.

Full evidence and the post-merge production gate are recorded in `docs/research/phase2-correlation-stability-shim-retirement-2026-09-17.md`. Maintenance no longer generates a recurring LibCST plan for this retired migration.

## `app.phase2_final_visibility` → `app.lifecycle_outcome_visibility_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical implementation exposes partial-media and fidelity-rejection outcomes in durable lifecycle state and observability. Production still imports `app.lifecycle_outcome_visibility_runtime` after `zero_silent_miss` and `phase2_runtime_compat`, immediately before `lifecycle_correlation_runtime` and Event Fusion.

The package-local binding `_phase2_final_visibility` still points directly to the canonical module. It does not recreate or import the retired legacy module path and is outside the focused module-path retirement.

### Behavior contract

Retirement does not change partial-media detection, `media_status="partial_failed"`, `media_partial_failure`, the partial-media observability event, fidelity rejection/manual-review classification, `translation_status="fidelity_rejected"`, `manual_review_required`, its observability event, idempotency markers, media return values, Telegram behavior, state/database schemas, providers, schedules, secrets, or production dependencies.

### Retirement evidence

The original PR #79 migration measured about 84.5% execution coverage across 13 test contexts. A fresh pre-removal plan found three compatibility-only references and no production importer, no order-sensitive legacy reference, and no parse error. PR #89 then retired the path and completed a real post-merge production run through restore, live providers, full monitor pass, checkpoint, encrypted backup/outcome upload, and state/database persistence.

Full evidence is recorded in `docs/research/phase2-final-visibility-shim-retirement-2026-09-17.md`. Maintenance no longer generates a recurring LibCST plan for this retired migration.

## `app.channel_part4_finalfix` → `app.channel_source_fact_normalization_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-16**  
Retired: **2026-09-17**

The canonical implementation owns source-authorized term normalization and the bounded English ordinal mapping used by hard-fact verification. Production imports `app.channel_source_fact_normalization_runtime` immediately after `channel_part4_hardening` and before `channel_part4_humanfix`, `channel_part4_qualityfix`, and `channel_part4_benchmark_hook`.

The package-local binding `_channel_part4_finalfix` still points directly to the canonical module. It does not recreate or import the retired module path and is outside the focused module-path retirement.

### Behavior contract

Retirement does not change ordinal values, semantic-number verification, source aliases/canonical Persian spellings, the source-authorization requirement, hashtag/@mention protection, replacement regex behavior, the hardening global binding, writer classes, fallback translation, human-gate fingerprints, providers, persisted state, Telegram delivery, schedules, secrets, or production dependencies.

### Retirement evidence

The original PR #80 migration measured 100% execution coverage across 12 test contexts. The fresh retirement audit found eight remaining dynamic/manual strings, all synthetic maintenance fixtures or registry data, with zero structural import, zero order-sensitive reference, zero parse error, and no Grimp importer/entrypoint chain. PR #87 retired the path and completed full post-merge production, Watchdog, Render, Security, CodeQL, and Maintenance validation.

Full evidence is recorded in `docs/research/channel-part4-finalfix-shim-retirement-2026-09-17.md`. Maintenance no longer generates a recurring LibCST plan for this retired migration.

## Completion sequence

The registered-shim completion sequence is durable in `docs/stage-c-completion-roadmap.md`:

1. channel source-fact legacy path — retired;
2. lifecycle outcome visibility legacy path — retired;
3. lifecycle correlation legacy path — retired in this focused step, pending its own post-merge production proof;
4. degraded-X recovery legacy path — retire last after the preceding proof is green;
5. run a fresh closure audit and explicitly classify the remaining historical implementation modules as either a future semantic-migration candidate or **retained by design**.

Stage D architecture-boundary enforcement remains deferred until that closure audit establishes stable intended boundaries.
