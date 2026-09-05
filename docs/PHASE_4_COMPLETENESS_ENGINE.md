# Phase 4 — Completeness Engine (shadow)

## Evidence and decision

Repository facts: Phase 1 persists raw conversions before relevance filtering. Phase 2 maintains source/window summaries but overwrites attempt rows and infers proof from a successful collector return. Recovery's raw API can return no response or an error-shaped response; neither is proof. Recovery checkpoints retain editorial updates, so an old checkpoint alone cannot certify raw observation continuity.

External reference: SQLite transactions document one simultaneous writer and `BEGIN IMMEDIATE` writer serialization. No new database/service is required. The exact pinned twscrape revision used by this repository paginates raw GraphQL responses by Bottom cursor and treats `limit` as a page-level stopping budget rather than an exact tweet count. Its generic tweet parser recursively collects Tweet objects from the response, so nested/self-quoted and pinned tweets cannot safely serve as timeline-boundary witnesses.

Decision: add immutable attempt history and explicitly named shadow watermarks to the existing Phase 2 ledger connection in `private-review.sqlite3`. Python gathers raw evidence; the version 1 Rust JSONL subprocess decides COMPLETE/PARTIAL/UNPROVEN and validates cursor advancement. Finish and cursor changes commit atomically. No production health, delivery, filtering, or legacy cursor authority changes.

## Proof rules

Every enabled source is planned before a window starts, including invalid empty handles as visible invalid-source rows. Sources are attempted sequentially through existing collection. Unattempted sources close as UNPROVEN/NotAttempted. A failure or cancellation preserves the attempted source and closes remaining sources explicitly.

A page is proof-eligible only when the provider payload is non-error and contains recognized timeline structure. `TimelineAddEntries` supplies the normal top-level timeline entries; `TimelinePinEntry` is tracked separately and is never a lower-bound witness. An explicit Bottom termination is accepted as terminal evidence. Absence of a Bottom cursor is accepted as exhaustion only for an otherwise validated page, matching the pinned twscrape paginator's own end condition.

COMPLETE requires all of the following:

1. at least one validated raw page;
2. no failed attempt;
3. no unverified resumed checkpoint continuity;
4. deterministic terminal proof: either validated provider exhaustion or a validated ordered lower-bound crossing;
5. every top-level source post that raw page structure says falls inside the requested window must have reached the Phase 1 observation path;
6. top-level timeline timestamps used for lower-bound proof must be monotonic in the raw TimelineAddEntries order.

The ordered lower-bound rule exists because normal active 24-hour windows should not need to scrape the account's entire history. It is deliberately stricter than the compatibility collector: pinned tweets and nested/self-quoted Tweet objects may be extracted and persisted, but cannot prove that pagination crossed the lower boundary. If a parser stops early and any expected in-window top-level ID was not observed, coverage is incomplete and the shadow result cannot be COMPLETE.

Missing responses, error payloads, unparseable top-level timeline entries, non-monotonic top-level ordering, missing expected observation IDs, stale/gapped cursor progression, successful process exit alone, and missing/incompatible Rust IPC all fail closed to PARTIAL or UNPROVEN. No claim of zero real-world misses is made for an UNPROVEN window.

Attempts preserve run/source/window/identity, retry count, traversal count, persisted observation IDs/count, expected in-window top-level IDs, missing expected IDs, retained timeline count, pagination cursor, provider-exhaustion/lower-bound evidence, proof kind, bounded error class/summary, and legacy status for comparison. Raw content stays in Phase 1 tables. Error text is deliberately limited to exception class to avoid sensitive provider payloads.

Only COMPLETE advances the shadow source watermark. Time bounds normalize to UTC; Rust compares instants. Gaps are recorded and prevent advancement. Older/equal results cannot replace newer proven metadata. Duplicate finalization is idempotent. A completed attempt is never overwritten by a retry.

## Runtime and recovery

`COMPLETENESS_ENGINE_MODE=shadow` is the default. `disabled` reports non-healthy/disabled and cannot certify completeness. Invalid values fail explicitly. `EDITORIAL_CORE_BINARY` selects the installed executable. Missing, timed-out, malformed, or incompatible IPC yields UNPROVEN/EditorialCoreUnavailable. Docker builds the executable in a Rust stage; non-container hosts must install it on PATH. GitHub jobs without it remain explicitly unproven in shadow.

Each collection exposes `last_completeness_report` and logs its run ID and coverage counts. Inspect persisted per-source evidence with:

```bash
python -m tools.report_completeness --db .state/private-review.sqlite3
python -m tools.report_completeness --db .state/private-review.sqlite3 --run-id RUN_ID
```

A hard process kill leaves ATTEMPTING visible and non-healthy. After confirming that run is no longer active, finalize it without touching another run:

```bash
python -m tools.report_completeness --db .state/private-review.sqlite3 --run-id RUN_ID --recover-interrupted-run
```

New attempts have fresh IDs and retain interrupted history. There is no global recovery operation that can erase another active source/run. A resumed legacy checkpoint remains deliberately non-authoritative until its earlier raw-page proof can be cryptographically/structurally chained into the new attempt; a fresh proof attempt can still certify that source without deleting earlier observations.

## Validation and rollout gate

Tests cover proof decisions, actual Python/Rust IPC, baseline-vs-shadow disagreement on invalid empty responses, provider exhaustion, ordered lower-bound proof, pinned tweet exclusion, observation-coverage gaps, missing executables, all configured sources, retries, interruptions, stale results, cursor gaps, duplicate finalization, and atomic rollback. Rust CI builds the executable and runs Python/Rust integration tests; ordinary Python environments explicitly skip executable-dependent integration tests if it is absent.

Before production authority can switch, review real source reports, validate provider page shapes/traversal coverage against known source timelines, demonstrate interruption/retry behavior on production state, and compare shadow completeness with the current collector over representative active and quiet sources. A green unit suite or container build alone does not satisfy that gate. Legacy authority remains in place until this evidence exists.
