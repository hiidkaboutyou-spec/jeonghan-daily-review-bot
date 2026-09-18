# X recovery integrity semantic migration — 2026-09-18

## Decision

Migrate:

`app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime`

with no recovery behavior change.

Baseline production main:
`b1894546f170a5297dc218a5a11c71b82f16371b`

The base recovery migration and retirement are already complete and independently
production-proven.

## Production prerequisite

PR #110 retirement was production-proven on exact main:
- Daily Review Bot #4208 / run `35345201547`: success;
- state + DB restore: success;
- project validation: success;
- runtime smoke: success;
- live production providers: success;
- one complete automatic monitor pass: success;
- DB checkpoint: success;
- encrypted backup creation/upload: success;
- production outcome upload: success;
- state + DB persistence: success;
- Maintenance #128, Security #136, CodeQL #97, Render #305,
  Fanfic #977, Workflow Safety #13, Watchdog #3311: success.

## Fresh planning evidence

Final PR #110 Maintenance #127 artifact:
- id: `10546004788`
- digest: `sha256:d71d09d6998691d3397f8f20904498718eb27221c76dea9fd69e584604b4040f`

LibCST plan:
- references: **6**
- structural: **2**
- dynamic/manual: **4**
- import-order-sensitive: **1**
- parse errors: **0**

Real structural callers:
1. `app/__init__.py`
2. `tests/test_phase3_recovery_coverage_precursor.py`

The four strings in `tests/test_module_family_evidence.py` are deliberately
synthetic historical-name fixtures used to test the maintenance inventory/report
tool. They are not runtime callers and must not be blindly rewritten.

Module-family evidence:
- risk: high / runtime-linked;
- direct importers: `app`;
- downstream importer count: 1;
- coverage: **99.2%**;
- test contexts: **33**.

## Implementation contract

The canonical `app/x_recovery_integrity_runtime.py` is copied byte-for-byte from
the pre-migration implementation blob
`4372a3efc848ac4140e956fc1bd55b871c489328`.

The historical `app/phase3_recovery_hardening.py` becomes a same-module-object
compatibility alias.

The canonical module continues to import only the already-canonical base recovery
module and to patch exactly these names at import time:
- `_update_dicts` → lossless checkpoint update serialization;
- `_sanitize_checkpoint` → no-age-expiry integrity sanitizer;
- `_lookup_user` → bounded profile lookup with scoped exact-author identity recovery.

No second checkpoint/state/observability authority is introduced.

## Non-negotiable invariants

- persisted key `x_retrieval_checkpoints` unchanged;
- `CHECKPOINT_VERSION` unchanged;
- checkpoint ID derivation and normalization unchanged;
- source-scoped retry/fallback behavior unchanged;
- incomplete-window cursor retention unchanged;
- source-authority boundary unchanged;
- exact package order remains:
  configured-source layers → base resumable recovery → recovery integrity →
  degraded-provider recovery → source ledger → later completeness provider-proof/runtime.

## Dependency decision

No new runtime or maintenance dependency is needed. Existing LibCST, Grimp,
Coverage.py, Ruff, full regression suites, Workflow Safety, Security, CodeQL and
Render validation cover this semantic rename. Adding another package would add
supply-chain surface without closing a demonstrated gap.

## Next gate

After merge, require independent real-main production proof and a fresh final
reference/import audit. Only then may the historical integrity shim be retired in
a separate focused PR.
