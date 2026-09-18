# X recovery semantic-boundary planning — 2026-09-18

## Decision

Plan the two recovery modules together, but migrate them **sequentially**.

1. First migrate `app.phase3_recovery` → `app.x_resumable_recovery_runtime`.
2. Keep `app.phase3_recovery` as a same-module-object compatibility alias during that migration.
3. Update `completeness_provider_proof` and `phase3_recovery_hardening` to import the canonical base recovery module while preserving their local binding names.
4. Preserve all existing StateStore checkpoint keys/schema semantics and package install order.
5. Merge and independently production-prove the base migration.
6. Only then migrate `app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime` in a separate focused change.

Do **not** migrate both modules in one implementation PR. Their import-time mutation contract makes a combined move harder to prove and risks split module state.

## Baseline

Production main before this planning pass:

`13274d15496e81b1e1a2de70b8197476fd46d6fc`

PR #105 completed the critical coverage precursor without production-code changes:
- `app.phase3_recovery`: 76.6% across 129 contexts;
- `app.phase3_recovery_hardening`: 99.2% across 33 contexts.

The remaining large base misses are dominated by implementation bodies intentionally superseded at package startup, so coverage inflation is not a migration gate.

## Fresh dual LibCST evidence

Planning PR #106 head `2ee14c0f363103f18711ba9f8ffb9ccff2406a9c`.

Hani Maintenance Diagnostics #117 / run `35342037236`.

Artifact:
- id: `10544609684`
- digest: `sha256:2534e37c12df3024a648c23c47069734eb90447958188b1bc2f5648401ad8f4a`

### Base recovery

`app.phase3_recovery` → `app.x_resumable_recovery_runtime`

- references: **56**
- structural references: **11**
- manual/dynamic references: **45**
- import-order-sensitive references: **1**
- parse errors: **0**
- direct importers: **3**
  - `app`
  - `app.completeness_provider_proof`
  - `app.phase3_recovery_hardening`

The high dynamic count is mostly test patch/import strings. It is not a runtime importer count and must be reviewed rather than mechanically rewritten.

### Integrity recovery

`app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime`

- references: **6**
- structural references: **2**
- manual/dynamic references: **4**
- import-order-sensitive references: **1**
- parse errors: **0**
- direct importers: **1** (`app`)

## Import-time mutation contract

`phase3_recovery_hardening` imports the base module and mutates these names on that exact module object:
- `_update_dicts`
- `_sanitize_checkpoint`
- `_lookup_user`

`completeness_provider_proof` independently imports the same base module and replaces:
- `_provider_page`

Therefore both consumers must converge on the **same canonical base module object** before the legacy base path can ever be retired.

The safe package order remains:

`source_authority_hardening`
→ configured-source layers
→ base resumable recovery
→ recovery integrity hardening
→ degraded-provider recovery
→ source ledger
→ later completeness provider-proof/completeness layers.

The existing relative recovery order in `app/__init__.py` must not move.

## Durable-state contract

The semantic migration must not rename or reinterpret persisted state.

Required invariants:
- StateStore key remains `x_retrieval_checkpoints`;
- checkpoint `version` remains governed by `CHECKPOINT_VERSION`;
- checkpoint IDs remain derived from source + window start + include-replies;
- schema normalization continues to preserve older valid state and conservatively discard malformed state;
- exact/older compatible checkpoint lookup semantics remain unchanged;
- incomplete windows never gain permission to advance success cursors merely because a module path changed.

The module rename is an implementation ownership change, **not a persisted-data migration**.

## Compatibility design for the base move

The first implementation PR should:
- copy the base implementation byte-for-byte to `app/x_resumable_recovery_runtime.py`;
- reduce `app/phase3_recovery.py` to a same-module-object alias;
- migrate structural production callers to the canonical path;
- preserve local binding names during compatibility;
- register exactly one active Stage C compatibility shim;
- keep the integrity implementation body unchanged;
- require identity tests proving old and canonical base imports resolve to the exact same module object;
- require the existing recovery, completeness, source-authority and production workflow suites.

Only after independent real-main proof may the base shim be considered for later retirement. The integrity migration is a separate later change and must not start while the base migration is unproven.

## Tooling decision

No new runtime dependency is required for this migration. Existing LibCST, Grimp, Coverage.py, Ruff and the focused recovery regression suite expose the relevant failure surface.

Separate CI-hardening work may add workflow-only scanners, but it must remain isolated from the recovery migration.
