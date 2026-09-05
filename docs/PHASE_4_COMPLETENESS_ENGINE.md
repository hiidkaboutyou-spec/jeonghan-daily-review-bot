# Phase 4 — Completeness Engine

Status: **SHADOW EVIDENCE ENGINE IMPLEMENTED; PRODUCTION AUTHORITY NOT PROMOTED**

Phase 4 defines deterministic evidence for a source/window. It does not make a green
workflow, a successful Python return, an AI decision, or downstream editorial retention
equivalent to source completeness.

## REPO FACTS

- `config/sources.json` currently has 24 configured X sources: 23 enabled and one disabled.
- Every configured source currently requests replies.
- Phase 1 persists configured-source observations before `keyword_filter`, retweet exclusion,
  editorial dedupe, grouping, translation, media, or Telegram delivery can erase them.
- Phase 2 has a compatibility source ledger and per-source cursor, but its success inference
  predates evidence-backed Phase 4 and is not Phase 4 proof authority.
- Phase 3 owns deterministic state invariants in Rust and exposes them through versioned
  JSONL/subprocess IPC. Python remains responsible for X/twscrape and volatile network work.
- The actual collector uses the pinned twscrape revision
  `3f47af40bc2036fe96562e82edf52b92b7fc2574`, then the repository's resumable
  cursor/checkpoint layer in `phase3_recovery.py`.
- The Phase 4 runtime is shadow-only. It writes additive attempt/proof rows to the same
  `private-review.sqlite3`; it does not change Telegram posting, legacy delivery, or public behavior.
- Current `main` already contains the later Phase 5 queue. Phase 4 hardening changes only the
  truth/proof layer and tests; Phase 5 behavior is treated as existing baseline, not expanded here.

## EXTERNAL EVIDENCE

### Actual twscrape paginator

The pinned twscrape `_gql_items` implementation follows Bottom cursors but can stop for more
than one reason: no client/response, no Bottom cursor, stalled cursor/entries, an empty-page
budget, or a caller limit. Therefore ordinary generator termination is not a proof rule by
itself. The Phase 4 proof parser inspects the raw timeline structure rather than treating
"the async iterator ended" as COMPLETE.

The same pinned queue client rotates/locks accounts for rate limits and auth failures, retries
transport/load-shed failures, and can return `None`/abort for conditions that are not timeline
exhaustion. Phase 4 consequently fails closed when a response is absent, error-shaped, or lacks
recognized terminal timeline structure.

References:
- https://github.com/vladkens/twscrape/tree/3f47af40bc2036fe96562e82edf52b92b7fc2574
- `twscrape/api.py` and `twscrape/queue_client.py` at that exact revision.

### SQLite

SQLite documents that only one write transaction exists at a time, and `BEGIN IMMEDIATE`
acquires the write transaction at the start. Phase 4 uses `BEGIN IMMEDIATE` around the
read-current-cursor -> validate -> update-attempt/cursor transaction so another writer cannot
interleave a conflicting source watermark update.

SQLite UPSERT applies conflict handling to uniqueness constraints and a failing DO UPDATE uses
ABORT semantics. Phase 4 uses uniqueness keys for attempts/observations/cursors and relies on the
surrounding transaction for atomic rollback.

References:
- https://www.sqlite.org/lang_transaction.html
- https://www.sqlite.org/lang_upsert.html

### Python / Rust boundary

Python's `sqlite3.Connection` context manager commits on clean exit and rolls back an open
transaction on an uncaught exception. Rust/chrono parses RFC3339 cursor timestamps and compares
instants; Phase 4 does not delegate source completeness to an LLM.

Reference:
- https://docs.python.org/3/library/sqlite3.html

## PROJECT DECISIONS

1. Keep collection source-first and proof per configured source/window.
2. Keep Python as provider-observation authority and Rust as deterministic verdict/cursor authority.
3. Keep versioned JSONL/subprocess IPC; no PyO3, Redis, PostgreSQL, Kafka, or second truth database.
4. Persist proof history immutably; retry creates a new attempt.
5. COMPLETE requires validated terminal evidence plus observation coverage.
6. PARTIAL/UNPROVEN/ATTEMPTING never advance the Phase 4 shadow complete-through cursor.
7. Store explicit attempt timing/number and `cursor_before`, `cursor_candidate`, `cursor_after`,
   and `cursor_advanced` so a report can answer why a watermark moved or did not move.
8. Validate top-level timeline order across pages, not just within one page. Cursor pages may
   overlap IDs; cross-page ordering therefore compares only newly observed top-level IDs.
9. Keep the engine shadow-only until representative real-source evidence is reviewed. A CI-green
   state proves implementation invariants, not X's real-world server completeness.

## 1. Provider/source proof semantics

Window semantics are `[window_start, window_end)`. Inputs are timezone-aware and normalized to a
fixed UTC RFC3339 representation before persistence. The Rust cursor primitive independently parses
RFC3339 instants.

Proof-eligible data comes from the raw UserTweets/UserTweetsAndReplies timeline response:
- normal `TimelineAddEntries` provide ordered top-level post IDs;
- `TimelinePinEntry` may contribute an in-window expected observation but never proves ordering;
- nested/self-quoted tweets extracted by twscrape are observations, not boundary witnesses;
- explicit Bottom termination is terminal evidence;
- absence of a Bottom cursor is terminal only when the page itself is structurally valid.

For lower-bound proof, every top-level page must be monotonic and cross-page progression must remain
monotonic after ignoring overlapping duplicate IDs. Every expected in-window top-level ID must have
reached the Phase 1 observation path.

## 2. COMPLETE / PARTIAL / UNPROVEN rules

### ATTEMPTING

The source/window attempt started, but no final deterministic verdict exists. It cannot advance a
cursor and is unhealthy for completeness.

### COMPLETE

COMPLETE requires:
- at least one validated raw page;
- no provider/core failure;
- no unverified resumed-checkpoint continuity;
- complete expected-observation coverage;
- valid traversal ordering;
- either validated provider exhaustion or a validated ordered lower-bound crossing.

Proof kinds include:
- `validated_provider_exhaustion`
- `validated_ordered_lower_boundary`

### PARTIAL

PARTIAL means useful provider rows were traversed but complete coverage was not proven. Examples:
mid-pagination network/rate/cursor/parser failure, observation-coverage gap, invalid ordering, or
ambiguous termination after some raw rows. Existing observations remain durable.

Proof kinds are bounded and explanatory, for example:
- `partial_provider_failure`
- `partial_observation_coverage_gap`
- `partial_timeline_order_invalid`
- `partial_unproven_termination`

### UNPROVEN

UNPROVEN means there is insufficient reliable source evidence to certify the requested window.
Examples: auth/provider failure before useful traversal, no validated page, incompatible/missing
Rust core, interrupted/not-attempted source, or ambiguous zero-row termination.

Proof kinds include:
- `unproven_provider_failure`
- `unproven_no_validated_page`
- `unproven_invalid_provider_response`
- `unproven_interrupted`
- `unproven_not_attempted`

AI/model confidence has zero authority over these states.

## 3. Cursor advancement rules

The Phase 4 cursor is source-specific and shadow-only.

- COMPLETE is the only state eligible to advance.
- PARTIAL, UNPROVEN and ATTEMPTING never advance.
- A source can update only its own cursor row.
- The candidate is the COMPLETE window end.
- A gap cannot be skipped.
- A cursor may remain equal or move forward; it never moves backward.
- An older/equal COMPLETE attempt cannot replace newer cursor metadata.
- Cursor read, Rust validation, cursor write and attempt finalization are one serialized transaction.
- Every finalized attempt records before/candidate/after/advanced explicitly.

The older Phase 2 compatibility cursor remains separate and is not used as Phase 4 proof authority.

## 4. Retry semantics

Retry is source/window-specific and creates a new immutable attempt. Prior observations and proof
rows are not erased. A fresh retry that proves the same previously incomplete window COMPLETE may
advance only that source. Other sources' attempts/cursors remain unchanged.

Legacy resumable checkpoints are intentionally conservative: a resumed segment is not automatically
accepted as new Phase 4 completeness proof because the earlier raw-page proof is not cryptographically
chained into the new attempt. A later fresh proof can certify the window without deleting old raw
observations.

## 5. Crash semantics

A process crash after observations/checkpoints but before final verdict never implies COMPLETE.
The attempt remains ATTEMPTING on disk and therefore non-healthy. After an operator verifies that
the run is no longer active, `tools.report_completeness --recover-interrupted-run` finalizes only
that run's unfinished rows as UNPROVEN. Observations remain.

## 6. Empty-window semantics

Zero posts is valid.

- validated provider page + deterministic terminal evidence + zero in-window observations => COMPLETE;
- zero observations + missing/error/ambiguous provider evidence => UNPROVEN, never COMPLETE.

## 7. Known provider limitations

This engine proves completeness only to the strongest deterministic condition exposed by the
actual pinned provider representation. It cannot prove that X itself never omitted an item from a
response that otherwise appears structurally valid. It also does not treat generic X search as a
timeline completeness authority.

Retweets, replies, quotes, media-only posts, generic captions and later relevance decisions remain
raw observations when the provider exposes them. Their downstream editorial treatment does not
change collection proof.

Media download success is a separate later-pipeline concern and cannot redefine source completeness.

## 8. Rust / Python responsibility

Python:
- X/twscrape/profile lookup/network calls;
- raw timeline structure inspection;
- raw observation persistence/linkage;
- factual traversal evidence;
- SQLite orchestration around the existing ledger connection.

Rust:
- COMPLETE/PARTIAL/UNPROVEN deterministic state evaluation;
- RFC3339 instant parsing;
- COMPLETE-only cursor advancement and monotonicity invariants.

The IPC contract remains version 1 and evidence records carry an explicit evidence version.

## 9. Shadow rollout behavior

`COMPLETENESS_ENGINE_MODE=shadow` is the default. `disabled` never certifies a source.

Shadow output is inspectable through:
```bash
python -m tools.report_completeness --db .state/private-review.sqlite3
python -m tools.report_completeness --db .state/private-review.sqlite3 --run-id RUN_ID
```

A GitHub Actions success or legacy `HEALTHY` result must never be displayed or consumed as equivalent
to `all configured Phase 4 source rows are COMPLETE`.

Promotion to production completeness authority requires representative live-source review of active
and quiet accounts, including page-shape/termination evidence and interruption/retry behavior.
That rollout observation is an operational gate separate from unit/CI correctness.

## 10. Test matrix

The canonical Python suite now executes the Phase 2 source-ledger regressions under `unittest`
instead of leaving pytest-style module functions undiscovered.

Phase 4 acceptance coverage includes:
1. normal exhaustion;
2. ordered lower-bound crossing;
3. proven empty;
4. successful multi-page traversal;
5. network failure after observations;
6. rate-limit failure after observations;
7. cursor failure after observations;
8. conversion/parser failure after observations;
9. auth failure before useful traversal;
10. provider unavailable before useful traversal;
11. ambiguous termination;
12-17. COMPLETE-only cursor safety, per-source isolation, monotonic/equal/stale metadata;
18-20. failing-source isolation, source-specific retry, adjacent windows;
21-24. observation survival, retry idempotency, interruption/crash safety;
25-30. filtering/media/reply/quote/retweet/NOT_RELEVANT independence from proof;
31-35. half-open boundaries, timezone normalization, equal/stale cursor cases;
36-37. proven-empty versus unproven-empty;
plus the critical A COMPLETE / B PARTIAL / C COMPLETE -> retry B -> B COMPLETE fixture.

The Rust workflow runs:
- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets --all-features -- -D warnings`
- `cargo test --workspace --all-features`
- both Phase 4 Python completeness test modules against the built JSONL executable.

The main workflow still runs the repository's canonical compile, `app --check`, and full unittest suite.

## 11. Rollback path

The hardening is additive:
- disable Phase 4 with `COMPLETENESS_ENGINE_MODE=disabled` if the shadow path itself causes runtime trouble;
- revert the Phase 4 hardening commit/PR to restore the earlier shadow schema/logic;
- additive attempt columns may remain harmlessly unused;
- no legacy source cursor, Telegram posting behavior, source configuration, credentials, or raw observation rows need deletion.

Rollback must never delete observations or rewrite source history.
