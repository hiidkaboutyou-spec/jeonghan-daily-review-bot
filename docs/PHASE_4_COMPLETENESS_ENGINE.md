# Phase 4 — Completeness Engine (shadow)

## Evidence and decision

Repository facts: Phase 1 persists raw conversions before relevance filtering. Phase 2 maintains source/window summaries but overwrites attempt rows and infers proof from a successful collector return. Recovery's raw API can return no response or an error-shaped response; neither is proof. Recovery checkpoints retain editorial updates, so an old checkpoint alone cannot certify raw observation continuity.

External reference: [SQLite transactions](https://www.sqlite.org/lang_transaction.html) document one simultaneous writer and `BEGIN IMMEDIATE` writer serialization. No new database/service is required.

Decision: add immutable attempt history and explicitly named shadow watermarks to the existing Phase 2 ledger connection in private-review.sqlite3. Python gathers raw evidence; the version 1 Rust JSONL subprocess decides COMPLETE/PARTIAL/UNPROVEN and validates cursor advancement. Finish and cursor changes commit atomically. No production health, delivery, filtering, or legacy cursor authority changes.

## Proof and limitations

Every enabled source is planned before a window starts, including invalid empty handles as visible invalid-source rows. Sources are attempted sequentially through existing collection. Unattempted sources close as UNPROVEN/NotAttempted. A failure or cancellation preserves the attempted source and closes remaining sources explicitly.

COMPLETE currently requires validated raw terminal-page evidence with no failed attempt, no lower-bound early exit, and no resumed legacy checkpoint. Validation requires a non-error response containing a TimelineAddEntries entries array. Missing responses, legacy generator exit, zero retained posts, and successful process exit do not qualify. All visited page responses must validate. This intentionally produces conservative false negatives: lower-bound/pinned timeline ordering and resumed raw-continuity proof are not certified yet. No claim of zero real-world misses is made.

Attempts preserve run/source/window/identity, retry count, traversal count, persisted observation IDs/count, retained timeline count (before later presentation filtering), pagination cursor, proof kind, bounded error class/summary, and legacy status for comparison. Raw content stays in Phase 1 tables. Error text is deliberately limited to exception class to avoid sensitive provider payloads.

Only COMPLETE advances the shadow source watermark. Time bounds normalize to UTC; Rust compares instants. Gaps are recorded and prevent advancement. Older/equal results cannot replace newer proven metadata. Duplicate finalization is idempotent. A completed attempt is never overwritten by a retry.

## Runtime and recovery

`COMPLETENESS_ENGINE_MODE=shadow` is the default. `disabled` reports non-healthy/disabled and cannot certify completeness. Invalid values fail explicitly. `EDITORIAL_CORE_BINARY` selects the installed executable. Missing, timed-out, malformed, or incompatible IPC yields UNPROVEN/EditorialCoreUnavailable. Docker builds the executable in a Rust stage; non-container hosts must install it on PATH. GitHub jobs without it remain explicitly unproven in shadow.

Each collection exposes `last_completeness_report` and logs its run ID and coverage counts. Inspect persisted per-source evidence with:

```
python -m tools.report_completeness --db .state/private-review.sqlite3
python -m tools.report_completeness --db .state/private-review.sqlite3 --run-id RUN_ID
```

A hard process kill leaves ATTEMPTING visible and non-healthy. After confirming that run is no longer active, finalize it without touching another run:

```
python -m tools.report_completeness --db .state/private-review.sqlite3 --run-id RUN_ID --recover-interrupted-run
```

New attempts have fresh IDs and retain interrupted history. There is no global recovery operation that can erase another active source/run.

## Validation and rollout gate

Tests cover proof decisions, actual Python/Rust IPC, baseline-vs-shadow disagreement on empty responses, missing executables, all configured sources, retries, interruptions, stale results, gaps, duplicate finalization, and atomic rollback. Rust CI builds the executable and runs integration tests; ordinary Python environments explicitly skip executable-dependent integration tests if it is absent.

Before production authority can switch, review real source reports, validate provider page shapes and traversal coverage against known source timelines, resolve conservative proof limitations, and demonstrate interruption/retry recovery on production state. A green unit suite or container build alone does not satisfy that gate. Legacy authority remains in place until this evidence exists.
