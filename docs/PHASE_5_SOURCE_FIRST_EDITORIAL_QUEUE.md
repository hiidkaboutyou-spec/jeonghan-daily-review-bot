# Phase 5 — Source-first Editorial Queue

## Research and repository audit

The existing private inbox is durable but time-first: `review_inbox.list_items()` orders rows by `created_at DESC`, so normal review can interleave accounts. It has no durable concept of the active source, source progress, explicit source defer/resume, or source-scoped retry.

The repository already has the storage pieces Phase 5 needs:

- `review_inbox` is authoritative for per-draft `pending/ready/rejected` state;
- Phase 1/2/4 truth lives in the same `private-review.sqlite3` file;
- the crash/restart contract already states that a second queue technology is not justified for the single authoritative GitHub Actions runtime;
- `config/sources.json` is the configured-source authority and its file order is stable;
- Phase 4 completeness is intentionally shadow-only and must not become production cursor/delivery authority without real-source rollout evidence.

External evidence used before implementation:

- SQLite documents transactions as atomic/consistent/isolated/durable, including across crashes: https://www.sqlite.org/transactional.html
- SQLite-backed durable queue implementations demonstrate two useful semantics without requiring us to adopt their framework: ordered work can be partitioned by a logical key, and deliberate deferral is distinct from retry/failure. Example: https://github.com/torkbot/sledge

A video tutorial would not add authoritative information for this narrow persistence/state-machine decision, so Phase 5 does not cargo-cult a YouTube implementation.

## Decision

Use one additive SQLite orchestration layer over the existing inbox. Do **not** add Redis, Celery, Kafka, a hosted queue, or a second database.

`review_inbox.status` remains the editorial truth for each draft. Phase 5 stores only:

- one durable active review round;
- stable configured source membership/order;
- the current active source;
- immutable source/draft membership for that round;
- explicit source defer/resume state;
- bounded source retry metadata.

This makes Phase 5 rollback-safe: removing the orchestration layer cannot erase captions, drafts, archive rows, seen state, delivery receipts, raw observations, source-ledger evidence, or completeness evidence.

## Source-first invariants

1. Configured file order is the editorial source order. Retrieval `priority` does not silently reshuffle review.
2. Within a source, pending drafts are reviewed oldest-to-newest with `draft_id` as deterministic tie-breaker.
3. One source remains active while it has pending drafts.
4. A later source cannot become active merely because its post is newer.
5. The admin may explicitly defer the active source; only then does the queue move around it.
6. Resuming a deferred source does not interrupt a different source already active; it rejoins stable order for the next transition.
7. New work for a previously completed earlier source reopens that source but does not steal focus from the current source mid-review.
8. Source retry scans only that source and never force-replays already-seen Telegram deliveries.
9. Phase 4 COMPLETE/PARTIAL/UNPROVEN is displayed as shadow evidence. Phase 5 does not promote it to production cursor/delivery authority.
10. The old chronological/event-first inbox remains an explicit compatibility view during rollout.

## UX

Normal `/inbox` / `📥 پیش‌نویس‌ها` opens the source-first queue.

The screen shows:

- source progress (`source N / total`);
- post progress inside the active source;
- every source's editorial state and Phase 4 shadow proof status;
- open-current-post;
- defer active source;
- retry only that source;
- resume deferred source;
- chronological compatibility view.

The source-first draft view keeps existing rewrite/copy/reject actions. Draft actions continue to be handled by the established private-review code; Phase 5 only resynchronizes navigation afterward.

## Retry boundary

`retry source` requests a fresh bounded 24-hour collection for exactly that handle. Retrieved updates are delivered with `force=False`, so already-seen updates remain deduplicated. A retry failure stores only a bounded exception class, not provider payloads, cookies, tokens, or credentials.

Deferral and retry are deliberately separate:

- **defer** means the admin intentionally wants to review another source first;
- **retry** means collection for this source should be attempted again.

A failed retry does not silently mark the source reviewed or discard its drafts.

## Validation gate

Phase 5 must not merge until current-head CI proves:

- configured order beats chronology/priority;
- a source cannot be interleaved with a later source;
- explicit defer moves to the next source;
- resume does not steal active focus;
- restart preserves active/deferred state;
- new earlier-source work does not interrupt the active source;
- Phase 4 proof remains informational;
- retry state persists;
- Telegram callback payloads remain within the 64-byte limit and expose no public publish action;
- the existing test suite remains green.

After merge, Phase 6 may change relevance presentation, but it must not bypass this source-first queue or erase observed posts.
