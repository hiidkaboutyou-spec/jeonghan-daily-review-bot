# X resumable recovery semantic migration — 2026-09-18

## Scope

This change moves the implementation responsibility from `app.phase3_recovery` to
`app.x_resumable_recovery_runtime` without changing production recovery behavior.

Baseline production main:

`ba3bdddd4984f0c565b9ae70c1c445197cea7aae`

Planning evidence:
- PR #105 coverage precursor: base 76.6% / 129 contexts; integrity 99.2% / 33 contexts.
- PR #106 dual LibCST plan: base 56 references (11 structural / 45 dynamic-manual /
  one import-order-sensitive / zero parse errors); integrity six references.
- `phase3_recovery_hardening` patches `_update_dicts`, `_sanitize_checkpoint`, and
  `_lookup_user` on the base module object.
- `completeness_provider_proof` patches `_provider_page` on that same object.

## Implementation

- `app/x_resumable_recovery_runtime.py` is the former base implementation copied
  byte-for-byte.
- `app/phase3_recovery.py` is reduced to a same-module-object compatibility alias.
- Package startup, integrity hardening, and completeness provider-proof import the
  canonical module directly while preserving their existing local binding names.
- Focused tests and patch strings target the canonical path; the migration-registry
  suite retains the explicit legacy/canonical identity contract.
- Maintenance coverage measures the canonical base implementation.
- Recurring LibCST planning tracks only the active base migration. The integrity move
  is deliberately blocked while this shim is active.

## State and behavior invariants

No persisted-state migration is introduced.

The following remain unchanged:
- StateStore key `x_retrieval_checkpoints`;
- `CHECKPOINT_VERSION`;
- checkpoint ID derivation;
- checkpoint normalization and conservative malformed-state discard;
- exact/older compatible checkpoint lookup;
- bounded source/page retries;
- syndication fallback accounting;
- source-authority filtering;
- incomplete-window cursor retention;
- collector and Application state-binding installation;
- configured-source → base recovery → integrity hardening → degraded recovery →
  source ledger → completeness proof/runtime ordering.

The old and canonical imports must resolve to the exact same module object so all
import-time patches and mutable globals remain singular.

## Next gate

Do not start the integrity migration yet.

After this migration merges:
1. require a real production `main` run through restore, validation, runtime smoke,
   live providers, full monitor pass, DB checkpoint, encrypted backup/outcome upload,
   and state/DB persistence;
2. generate a fresh legacy-reference plan;
3. retire `app.phase3_recovery` in a separate focused PR only if the removal gates
   are satisfied;
4. only after that retirement may
   `app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime` become the
   active Stage C migration.
