# X resumable-recovery semantic migration — 2026-09-18

## Scope

Semantic Stage C base-recovery migration:

`app.phase3_recovery` → `app.x_resumable_recovery_runtime`

The implementation body is copied byte-for-byte unchanged. The historical module becomes a same-module-object compatibility alias. The integrity layer remains `app.phase3_recovery_hardening` in this PR.

## Preconditions

PR #105 completed the critical recovery coverage precursor without modifying production code:
- base recovery: **76.6% / 129 test contexts**;
- integrity hardening: **99.2% / 33 test contexts**.

PR #106 then fresh-planned both semantic boundaries with Hani Maintenance Diagnostics #117 / run `35342037236`, artifact `10544609684`, digest `sha256:2534e37c12df3024a648c23c47069734eb90447958188b1bc2f5648401ad8f4a`.

Base plan:
- 56 total references;
- 11 structural;
- 45 dynamic/manual;
- 1 import-order-sensitive;
- 0 parse errors;
- 3 direct importers: `app`, `app.completeness_provider_proof`, `app.phase3_recovery_hardening`.

All 45 dynamic references are test patch/import strings, not workflow/config/runtime callers.

Integrity planning showed that `phase3_recovery_hardening` must remain a separate later migration.

## Same-module-object requirement

The historical file is reduced to an alias using:

`sys.modules[__name__] = _implementation`

This is mandatory because two later startup layers mutate the base recovery module object:

`phase3_recovery_hardening` replaces:
- `_update_dicts`;
- `_sanitize_checkpoint`;
- `_lookup_user`.

`completeness_provider_proof` replaces:
- `_provider_page`.

Both layers are migrated to import `app.x_resumable_recovery_runtime` while preserving their existing local bindings, so all mutation targets remain one canonical module object.

## Import/install order

`app/__init__.py` imports `x_resumable_recovery_runtime` at the exact former `phase3_recovery` position. No recovery-layer ordering is intentionally changed.

The required relative order remains:
1. source/configured-source authority layers;
2. base resumable X recovery;
3. recovery integrity hardening;
4. degraded-provider recovery;
5. source ledger;
6. later completeness provider-proof/completeness layers.

## Persisted-state invariant

This migration is **not** a state migration.

The following remain unchanged:
- StateStore key `x_retrieval_checkpoints`;
- `CHECKPOINT_VERSION`;
- checkpoint-id derivation from source/window/include-replies;
- exact and older-compatible checkpoint selection;
- malformed/legacy checkpoint normalization;
- per-source retry and continuation semantics;
- source-authorized dedupe;
- success-cursor advancement restrictions.

No persisted key, JSON shape, SQLite schema, or recovery cursor is renamed.

## Test migration

The fresh LibCST plan identified 45 string-based test patch references. They are migrated deliberately to the canonical path so the tests exercise the real implementation directly rather than relying on the compatibility alias.

A separate migration-registry identity test keeps the old path covered only as a compatibility contract and proves old/canonical imports resolve to the exact same module object.

## Maintenance evidence

Focused Recovery Coverage now measures:
- `app/x_resumable_recovery_runtime.py`;
- `app/phase3_recovery_hardening.py`.

The recurring read-only LibCST plan remains enabled during the active compatibility phase so the final retirement audit can prove that the old path has no caller before deletion.

## Non-changes

No intentional change to:
- X API/provider behavior;
- retry counts/timing;
- checkpoint formats;
- source selection;
- degraded fallback;
- completeness logic;
- success cursor advancement;
- Telegram;
- AO3;
- state/database schema;
- dependencies or secrets.

## Required validation

Before merge:
- direct recovery and recovery-coverage tests;
- recovery-integrity tests;
- completeness/provider-proof tests;
- durable-state tests;
- migration identity/registry tests;
- full project validation;
- Maintenance / Security / Workflow Safety;
- Render production-image validation;
- relevant benchmark/fanfic workflows.

After merge, require real-main proof through state/database restore, project validation, runtime smoke, live providers, one complete monitor pass, checkpoint, encrypted backup/outcome upload, state/database persistence and workflow-triggered Watchdog.

Only after that proof and a fresh final reference audit may `app.phase3_recovery` be retired. The integrity semantic migration must not start before the base move is independently proven.
