# Real-Time Monitoring Architecture

Status: Phase implementation boundary / shadow contract
Date: 2026-08-14
Production baseline: `b13e070476cf9470d949633598f6bc5b52ac3d98`

## Goal

Add a fast detection layer without weakening the existing completeness system.

The production model remains:

```text
Fast detector (shadow first)
        |
        v
provider-neutral RealtimeCandidate
        |
        v
configured-source / reply / repost policy
        |
        v
stable logical identity = Update.id
        |
        v
authoritative hydration through the existing retrieval model
        |
        v
existing lifecycle when/if a future promotion is proven safe

independently and continuously:

GitHub Actions scheduled/watchdog retrieval
        |
        v
Phase 3 resumable backfill / checkpoints
        |
        v
Phase 2 Zero-Silent-Miss + cursor rules
        |
        v
same stable Update.id and existing private-review pipeline
```

The fast path is an accelerator, never the completeness authority.

## Production architecture observed at phase start

The real repository currently schedules Daily runs at the GitHub-supported five-minute
cron granularity. The application itself keeps a persistent
`scheduled_min_interval_minutes = 12` safety interval before another scheduled X scan is
due. A live GitHub Actions run also has runner startup work before retrieval: checkout,
Python setup, FFmpeg bootstrap, Python dependency installation, state and private SQLite
restore, runtime checks/provider preflight, and only then the automatic monitor pass.

The watchdog is a reliability control plane. It waits the configured production interval,
checks whether a newer Daily runtime exists, and can dispatch one bounded recovery when a
normal runtime is missing. It is not a low-latency detector.

Consequently, the present practical detection latency is not "five minutes." It is
bounded primarily by the twelve-minute application interval plus GitHub scheduling/queue
delay, Actions startup, provider request time, and downstream processing. Phase 3 then
provides complete/gap-safe recovery when a provider source fails.

## Options researched

### A. Keep existing GitHub Actions polling

Pros:
- already production-proven;
- no new paid X API dependency;
- handles all configured sources, replies, media metadata and Phase 3 recovery;
- strongest current completeness/backfill authority.

Cons:
- not low latency;
- each detection attempt pays GitHub runner/bootstrap overhead;
- scheduled workflow timing is not an exact real-time clock.

Decision: keep unchanged as backfill.

### B. Poll more frequently in GitHub Actions

GitHub scheduled workflows cannot be configured below the platform's five-minute
schedule granularity. Reducing the application's twelve-minute safety interval would
increase provider pressure while retaining runner startup and scheduler delay.

Decision: not selected as the real-time architecture. It adds load but does not create a
true fast path.

### C. Always-running polling process

An always-running Python process could poll more frequently and reuse one warm provider
session, avoiding per-run GitHub startup. It can support all configured sources using the
existing source policy.

Costs/risks:
- requires an actually always-on execution environment;
- the current unofficial/twscrape provider path remains provider/rate-limit sensitive;
- a separate independently delivering process would race with GitHub backfill unless the
  delivery claim becomes cross-process atomic;
- that cross-process state decision belongs in a later isolated Durable Queue/State
  phase only if shadow evidence proves it is needed.

Decision: viable future detector substrate, but no hosting migration in this phase.

### D. Official X Filtered Stream

Current official X documentation describes Filtered Stream as near real-time, with
roughly 6-7 seconds P99 stream latency, one pay-per-use connection and far more rule
capacity than the current 24 configured sources. Rules can be source-scoped and can
exclude reposts/replies as policy requires. Post fields/expansions can carry the identity,
reply/conversation and media metadata needed by `RealtimeCandidate`.

Current X API access is pay-per-use and requires a developer app/Bearer token plus
purchased credits. Each unique Post delivered by Filtered Stream counts toward usage.
This phase therefore does not enable it without explicit cost authorization.

Decision: technically the cleanest true fast detector, but access/cost gated.

### E. Repository-native hybrid

Build the normalized ingest contract, source-policy gate, stable-ID idempotency gate,
privacy-safe latency telemetry and shadow harness now. Keep provider and hosting choices
outside the business pipeline. Do not let shadow mode queue, send, advance a cursor, or
claim completeness.

Decision: selected. This is the smallest change that creates a real architectural seam
without pretending that GitHub cron is real-time or introducing a paid/hosting
dependency.

## Normalized ingest contract

`RealtimeCandidate` carries only detection metadata:

- configured source handle;
- stable post/source ID;
- source-created timestamp;
- detected timestamp;
- post URL;
- conversation/reply identity;
- reply/repost flags;
- optional media metadata;
- detection method;
- retrieval attempt correlation.

It deliberately does not contain channel captions, translation state, public-publish
state, a completeness claim, or a cursor.

Provider-specific detectors are adapters. The downstream logical identity remains the
existing `Update.id`.

## Authoritative hydration rule

A fast detector may have only partial metadata. Before any future delivery promotion,
the candidate must be hydrated/validated as an authoritative `Update`.

The hydrate boundary rechecks:

1. post ID is identical;
2. source/author is exactly the configured source;
3. configured reply policy is still satisfied;
4. repost candidates remain excluded;
5. authoritative content wins over shadow metadata;
6. candidate media may only fill a missing authoritative media list.

This prevents a stream/search payload from becoming a second implementation of retrieval
or content policy.

## Idempotency model

This project does not claim true exactly-once Telegram delivery.

The logical rule is:

```text
same Update.id
    -> same logical update
    -> seen or pending or already claimed?
    -> do not create another logical delivery
```

Existing `StateStore.queue_updates` already deduplicates pending items by stable ID and
the private lifecycle checks seen state. `LogicalUpdateGate` adds a lock-protected
process-local claim so two detector tasks inside one process cannot race.

Important boundary: a process-local lock is not a distributed transaction. If a future
always-running fast worker is allowed to deliver independently while GitHub Actions also
runs, cross-process atomic claim semantics must be solved before promotion. Shadow mode
does not create that race because it never queues or sends.

## Failure model

Fast detection is explicitly allowed to fail.

```text
fast detector fails
    -> record privacy-safe failure/defer signal
    -> make no completeness claim
    -> release any local hydration claim
    -> Phase 3 scheduled backfill remains authoritative
```

A Phase 3 partial/failure remains governed by the existing Phase 2/3 cursor and checkpoint
rules. The fast path cannot mark a failed window complete.

## Latency observability

The shadow layer adds allowlisted timing/correlation metadata only:

- `created_at`
- `detected_at`
- `detection_method`
- `processing_started_at`
- `private_delivery_at` (only when a real receipt is supplied)
- `detection_latency_ms`
- `end_to_end_latency_ms`
- `backfill_recovery`
- stable post/source/attempt IDs already allowed by observability

It does not log post bodies, captions, arbitrary URLs, headers, tokens, cookies or
provider secrets.

A future shadow detector should compare:

```text
post created -> fast detected
post created -> backfill detected
post created -> existing processing started
post created -> private Telegram receipt
```

without changing delivery behavior.

## Shadow-mode safety boundary

`REALTIME_SHADOW_MODE` defaults off.

`ShadowRealtimeIngestor` can:
- normalize observations;
- apply configured-source/reply/repost policy;
- process-local dedupe;
- authoritative hydration validation;
- latency/parity measurement.

It cannot:
- call Telegram;
- write the pending queue;
- advance `last_auto_run`;
- write the Phase 3 cursor/checkpoint;
- claim a window complete;
- publish publicly.

No provider adapter is wired into production in this PR. That is intentional: current
evidence says meaningful real-time operation requires either paid X Filtered Stream
access or an actually always-on polling host.

## Future compatibility

The contract intentionally retains `conversation_id`, `reply_to_id`, timestamps and media
items so a later Event/Timeline Fusion layer can reason over related observations without
changing detector identity.

No event fusion is implemented here.

Concert coverage must later preserve distinct performances, fancams, photos, videos,
interactions and stage moments. Therefore the fast path dedupes only the stable post
identity. It does not dedupe media merely because two posts are from the same event or
performance.

## Promotion gate

Before a fast path can become an early-delivery authority, shadow production evidence
must prove:

- actual detection latency is materially better than backfill;
- configured-source/reply/repost policy parity;
- authoritative hydration parity;
- no external-source leakage;
- no duplicate logical IDs across fast/backfill races;
- no content or secret leakage in observability;
- no Phase 2/3 cursor/completeness regression;
- no public publishing surface;
- a safe cross-process claim design if detector and delivery run in separate processes.

Until those gates are met, Phase 3 backfill remains the only completeness authority and
the existing private-review pipeline remains the only delivery authority.
