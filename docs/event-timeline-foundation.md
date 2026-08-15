# Long-form / Event Timeline Fusion Foundation

Status: **shadow-only development foundation**. This phase does not suppress or fuse Telegram output.

## Goal

The production-verified Event Fusion layer answers **which canonical Updates may belong to the same real-world Event**. This phase adds a separate, reversible question:

> Inside one Event, which Updates describe the same moment, a continuation/complementary moment, or a different moment, and in what deterministic order?

```text
Event E
├── Segment S1
│   ├── Update A
│   └── Update B
├── Segment S2
│   └── Update C
└── Segment S3
    ├── Update D
    └── Update E
```

Original `Update.id` records remain authoritative evidence.

## Audit of existing long-form logic

`organizer.py` already provides useful **processing chronology**, not semantic Segment identity:

- reply/conversation relationships outrank heuristic standalone Live grouping;
- standalone Live posts are grouped by author/day and separated after a four-hour gap;
- explicit part numbers are parsed;
- when every Live item has a part number, part ordering is preferred with timestamp/stable-ID tie-breakers;
- otherwise source-created time is the primary processing order.

Therefore `EventGroup.key` remains a processing/Telegram organizer identity. It is not `Event.id` and is not reused as `Segment.id`.

Event Fusion already provides durable semantic `Event.id`, configured-source-only fingerprints/memberships, reversible Event membership, and a conservative long-form guard so a common Live/show/interview container is not treated as the same moment by itself.

Timeline reuses these mechanisms rather than creating a second Event grouping system.

## Identity contract

```text
Event.id
  != Segment.id
  != Update.id
  != EventGroup.key
  != Phase-2 event_id
  != media identity
  != translation identity
  != Telegram delivery identity
```

`Segment.id` uses a `seg:` namespace and a deterministic hash of `Event.id` plus seed Update IDs. A streaming Segment retains its seed identity when later members attach. Explicit split/merge operations may produce a new deterministic Segment ID; those operations remain metadata-only and reversible.

## Segment matching

Comparison is allowed only inside the same Event.

Strong same-moment evidence includes:

- same quoted/original clip/post identity;
- same exact referenced media identity;
- same explicit content/episode timestamp within a narrow tolerance;
- same non-container clip/reference URL;
- same interview/question anchor.

Continuation/complementary evidence can include:

- direct reply;
- same thread/conversation;
- adjacent explicit part sequence;
- compatible topic overlap;
- shared participants;
- close source-created chronology.

Safety rules:

- time proximity alone never merges;
- generic text/topic overlap alone never merges;
- Event membership alone never merges;
- a reference repeated across most of a long-form Event is a **container reference**, not same-moment proof;
- uncertain cases stay separate/ambiguous;
- false semantic merges are considered more dangerous than false splits.

Recorded relationship labels are:

- `same_moment`
- `continuation`
- `complementary`
- `conflicting`
- `ambiguous`
- `separate`

This structure is for a later translation-fidelity phase. It does not choose a winning translation.

## Conflict handling

If strong same-moment evidence exists while configured sources disagree (for example incompatible explicit numeric facts or polarity), both Updates stay preserved and the relationship is stored as `conflicting` with unresolved status.

No synthesis is invented in this phase.

## Chronology

Segment order is deterministic and uses this evidence priority:

1. explicit content/episode timestamp;
2. explicit part/thread ordering;
3. source-created timestamp;
4. stable Segment-ID fallback.

Ingestion order is never the authority. Approximate/inferred timing is not promoted to an exact timestamp; stored order metadata records evidence kind/confidence.

## Content-type behavior

### Live

One Live can be one Event with many Segments. A shared Live/container reference does not collapse dinner talk, a Dokyeom topic, a game, and a later fan question into one moment.

### Going Seventeen / variety / reality

One episode/container can be one Event while intro, game, interaction and ending remain traceable Segments. A common episode URL is a container anchor unless stronger moment evidence exists.

### Interviews

Question identity, answer/clip reference, explicit content timestamp and source sequence can anchor Segments. Same interview/container alone cannot merge distinct questions/answers.

### Fansign / video call

Reports sharing a strong quoted clip/reference may describe the same interaction. Different fan/question anchors remain separate unless strong evidence links them.

### Concerts — coverage first

Timeline organization never becomes media dedupe:

- different fancams remain independent;
- different photos remain independent;
- different videos remain independent;
- different performances/interactions/stage moments remain independent;
- entrances/exits, backstage and official assets remain independent.

Event/Segment/performance identity is never a media-dedupe key. Existing exact-media identity remains the only narrow exact-duplicate mechanism. Several translations of the same spoken ment may share a Segment while their source Updates and media stay intact.

## Durable timeline state

No database, hosted service, queue or top-level StateStore schema migration is added.

The existing `event_fusion` namespace is extended additively:

```text
event_fusion
├── events
├── memberships
├── fingerprints
├── decisions
├── timeline_version
├── timeline_mode
├── segments
├── segment_memberships
├── timeline_fingerprints
├── segment_relationships
└── timeline_decisions
```

Timeline fingerprints store bounded IDs/hashes/chronology metadata only. They do not duplicate full post bodies, translated captions, media blobs, credentials, cookies or tokens.

If an Event member is from an earlier run, a missing bounded Timeline fingerprint may be rebuilt from the canonical archived Update without copying the post body into Timeline state.

The compatibility layer teaches Event Fusion's existing sanitizer/pruner to retain these fields while preserving the already verified top-level StateStore schema contract.

## Reversibility

Timeline metadata allows:

- `Update A: S1 -> S2` reclassification;
- split `S1 -> S1 + S2`;
- merge `S1 + S2 -> S3`.

These operations do not modify Update content/archive, Phase-2 lifecycle, seen state, pending delivery, Phase-3 checkpoints/completeness, media identity, translation identity or Telegram receipts.

If Event membership changes later, stale Segment membership is reconciled away as derived metadata.

## Shadow runtime boundary

Runtime order is:

```text
configured authoritative Updates
→ Event Fusion shadow grouping
→ Timeline shadow segmentation
→ existing private-review delivery unchanged
```

Timeline may identify Segment candidates, order Segments, persist metadata, and record overlap/conflict relationships. Timeline exceptions are caught and normal delivery continues.

It must not:

- suppress Telegram posts;
- replace several Updates with one message;
- choose/merge Persian translations;
- merge distinct media;
- change seen/delivered state;
- alter retrieval completeness/cursors/Phase-3 checkpoints;
- alter Telegram receipts or publication behavior;
- publish publicly.

## Benchmark

`data/event_timeline_benchmark.json` contains 30 deterministic difficult positive/negative cases covering Live, interviews, GOSE, variety/reality, fansigns, cross-language anchored evidence, conflicts, chronology, false-merge prevention and concert coverage.

`tools/run_event_timeline_benchmark.py` reports separately:

- same-moment precision;
- same-moment recall;
- false-merge count;
- false-split count;
- chronology accuracy;
- ambiguous deferral rate.

Any benchmark mismatch fails the command. A false merge is an explicit hard failure.

## Explicitly out of scope

This phase does **not** implement final translation selection/fusion, Persian style redesign, fused Telegram delivery, concert publication collapsing, media redesign, Forward-ready UX, Fast Detector, Go/Rust migration, Supabase/Redis/Celery/vector DB, paid LLM/embeddings/X API/hosting/database/queue.
