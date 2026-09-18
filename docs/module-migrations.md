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

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-18**  
Retired: **2026-09-18**

The canonical runtime owns the established human-quality-gate responsibility: source-fidelity checks, speaker/metadata repair, optional human-polish generation and acceptance, writer patching, benchmark resume invalidation, and the human-gate fingerprint contract.

The historical path was retained temporarily as a same-module-object compatibility alias because the downstream quality-repair layer mutates `HUMAN_GATE_VERSION`, `HUMAN_GATE_FINGERPRINT`, and `verify_hard_facts`. After the canonical runtime was independently production-proven and a fresh LibCST audit found only compatibility-registry test strings, the alias was removed in a focused retirement change.

The planning-first Maintenance run found eight structural references, zero dynamic/manual references, one import-order-sensitive reference (`app/__init__.py`), and zero parse errors. The coherent importer family is `app/__init__.py`, `channel_part4_qualityfix`, `channel_part4_benchmark_hook`, the human/quality/freshness tests, and the human benchmark tool. All known callers migrate to the canonical path while preserving their local binding names.

Behavior and install order are unchanged. The canonical runtime remains after `channel_part4_hardening` and `channel_source_fact_normalization_runtime`, and before `channel_part4_qualityfix` and `channel_part4_benchmark_hook`.

The legacy shim is now retired and must not be recreated. The detailed evidence is recorded in `docs/research/channel-human-quality-gate-shim-retirement-2026-09-18.md`. `channel_part4_qualityfix` is the next Stage C candidate, but only as a separate planning-first migration after this retirement receives independent post-merge production proof.


## `app.channel_part4_qualityfix` → `app.channel_quality_repair_runtime`

Status: **retired legacy path; canonical runtime only**  
Introduced: **2026-09-18**  
Retired: **2026-09-18**

Fresh planning in PR #101 measured 99.1% coverage across 129 test contexts and found four structural LibCST references, zero dynamic/manual references, one order-sensitive package-initializer reference, and zero parse errors. PR #102 then production-proved the prerequisite benchmark-freshness fix so the quality-repair implementation itself is bound into the human benchmark production fingerprint. PR #103 migrated the implementation byte-for-byte to the canonical path and received independent production proof.

The canonical runtime owns source-authorized translation quality repair and deterministic fallback hardening. Runtime order remains `channel_part4_hardening`, `channel_source_fact_normalization_runtime`, `channel_human_quality_gate_runtime`, `channel_quality_repair_runtime`, `channel_part4_benchmark_hook`.

The quality-repair runtime continues to mutate the canonical human-quality-gate `HUMAN_GATE_VERSION`, `HUMAN_GATE_FINGERPRINT`, and `verify_hard_facts` bindings. The human benchmark fingerprint tracks `app/channel_quality_repair_runtime.py`.

A fresh main-SHA retirement audit found zero importers/downstream importers and only two compatibility-registry test strings for the historical path. The shim is therefore retired and must not be recreated. Detailed retirement evidence: `docs/research/channel-quality-repair-shim-retirement-2026-09-18.md`.


## `app.phase3_recovery` → `app.x_resumable_recovery_runtime`

Status: **retired**  
Introduced: **2026-09-18**

PR #105 strengthened direct recovery coverage before any structural move. PR #106 then produced fresh dual-family LibCST/Grimp evidence and proved the base and integrity modules must migrate sequentially.

The canonical `app.x_resumable_recovery_runtime` contains the previous base implementation unchanged. The historical `app.phase3_recovery` path is retired and must remain absent.

Production callers `app`, `app.completeness_provider_proof`, and `app.phase3_recovery_hardening` now bind to the canonical base path. The integrity layer still owns its existing implementation and continues to patch `_update_dicts`, `_sanitize_checkpoint`, and `_lookup_user` on the canonical base module; `completeness_provider_proof` continues to replace `_provider_page` on that same object.

Persisted `x_retrieval_checkpoints`, checkpoint version/identity/normalization, retry semantics and cursor safety are unchanged. Migration evidence: `docs/research/x-resumable-recovery-semantic-migration-2026-09-18.md`. Retirement evidence: `docs/research/x-resumable-recovery-shim-retirement-2026-09-18.md`. The next candidate is planning-first `app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime`.

Do not retire the shim until independent main production proof and a fresh final reference audit. Do not begin the integrity semantic migration before the base migration is production-proven.

Stage D architecture-boundary enforcement remains deferred until the remaining explicitly approved Stage C migrations are complete or intentionally deferred.

## Active X recovery integrity migration

`app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime` is the
single active Stage C compatibility migration.

The canonical implementation is unchanged and continues to patch the canonical
`app.x_resumable_recovery_runtime` object at import time. The historical path is
a same-module-object alias during compatibility.

The four historical-name strings in `tests/test_module_family_evidence.py` are
synthetic maintenance fixtures, not callers, and intentionally remain unchanged.

No persisted recovery key/schema/retry/cursor semantics change. Retirement requires
independent production proof plus a fresh final reference/import audit.

Evidence:
`docs/research/x-recovery-integrity-semantic-migration-2026-09-18.md`.

