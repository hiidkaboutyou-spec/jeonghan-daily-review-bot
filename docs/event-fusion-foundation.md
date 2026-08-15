# Event Fusion Foundation

Status: **shadow grouping foundation only**. This phase does not implement Long-form/Event Timeline Fusion, segment fusion, translation selection, media merging, publication grouping, or any public publishing path.

## Purpose

Configured sources can independently report the same real-world occurrence. The Event layer records a conservative semantic relationship between those canonical source Updates without replacing them:

```text
Event E
  -> Update A
  -> Update B
  -> Update C
```

`Update.id` remains source truth. Event membership is derived, reversible metadata.

## Identity boundary

The new semantic `Event.id` uses its own `evt:` namespace and is independent from:

- `Update.id`;
- `EventGroup.key` from `organizer.py`;
- Phase 2 lifecycle `event_id` (which currently reflects the processing/draft `event_key`);
- retrieval-attempt IDs;
- translation job IDs;
- media identities;
- Telegram delivery keys and receipts.

Existing `EventGroup.key` continues to organize normal processing/delivery. It is not migrated or reinterpreted as semantic Event identity.

Event IDs are deterministic from the first accepted seed pair and remain stable when later members are added or removed. Membership changes never delete or rewrite canonical Updates.

## Taxonomy

The intentionally small taxonomy is:

- `unknown`
- `live`
- `interview`
- `variety`
- `reality`
- `going_seventeen`
- `fansign_or_video_call`
- `concert`
- `award_show`
- `brand_event`
- `airport_or_public_appearance`
- `official_content`
- `social_update`
- `other`

`unknown` is a valid result. The matcher must not invent a type merely to improve a grouping score.

## Matching signals

The shadow matcher uses deterministic, explainable signals only.

Strong/direct signals:

- direct reply relationship;
- same real conversation/thread identifier;
- shared quoted/original post identifier;
- same embedded external/original-content reference (persisted only as a hash).

Supporting signals:

- shared event-specific context anchor/hashtag;
- normalized topic-token overlap;
- meaningful shared SEVENTEEN participants other than Jeonghan alone;
- compatible event type;
- temporal proximity.

Time proximity alone never groups Updates. Similar wording alone never forces a group. Generic facts such as both posts mentioning Jeonghan are intentionally insufficient.

No embedding service, vector database, paid LLM, or paid API is used.

## Confidence and decisions

Each evaluated candidate records only bounded technical metadata:

- confidence;
- signal names;
- conflict names;
- decision;
- member Update IDs;
- semantic Event ID when a group is accepted.

Decisions are:

- `confident_same_event`
- `probable_same_event`
- `ambiguous`
- `separate_event`

Only confident/probable matches create or extend an Event. Ambiguous candidates remain separate. The policy intentionally prefers under-grouping to false semantic merges.

Long-form content (Lives, interviews, Going Seventeen, variety/reality) is treated especially conservatively when the evidence only proves a common container rather than the same spoken moment. The later Long-form/Event Timeline phase can add Segment relationships without changing today's Event IDs.

## Durable state

No new database is introduced. The existing durable JSON `StateStore` gains one bounded `event_fusion` namespace:

```text
event_fusion
  version
  mode = shadow
  events[event_id]
    event_id
    event_type
    created_at / updated_at
    member_update_ids
    confidence
    status
    subject_key
  memberships[update_id]
    event_id
    confidence
    matching_signals
    conflicts
    decision
  fingerprints[update_id]
    source/update/time correlation metadata
    hashed references/topics/anchors
    participant codes
  decisions[]
```

Full post bodies, translated captions, media URLs, cookies, tokens, and secrets are not duplicated into Event state. Member IDs point back to canonical Updates.

The Event namespace is persisted by the same atomic StateStore save/restart path already production-verified by Durable State Foundation. It does not own `pending_delivery`, `seen`, Phase 2 lifecycle, Phase 3 checkpoints, drafts, SQLite receipts, or archive source truth.

## Reversible membership

Membership can be removed or reclassified without changing:

- source Update/archive data;
- media;
- seen state;
- pending queue;
- Phase 2 lifecycle;
- Phase 3 completeness/checkpoints;
- translation identity;
- Telegram delivery receipt.

An Event may temporarily retain one member after a correction. This is acceptable shadow metadata and avoids cascading mutations of unrelated membership. An empty Event is removed.

## Concert safety invariant

Concert Event grouping is association, never media deduplication.

The following stay independently addressable even when they belong to the same concert or performance:

- different performances;
- different fancams;
- different photos;
- different videos;
- interactions and stage moments;
- entrances/exits;
- backstage content;
- official content.

`Event.id` and any future performance identity are forbidden as media-deduplication keys. Existing exact-media identity remains unchanged. Two different media assets remain different; an actual exact-media duplicate continues to use the existing exact-media rule.

Semantic fusion of duplicate speech/ments/translations is explicitly deferred.

## Shadow runtime behavior

The Event layer wraps the existing private-review delivery boundary only to observe the same already-authorized Update batch and persist candidate grouping metadata before normal delivery continues.

It does **not**:

- suppress an Update;
- reorder normal delivery;
- replace several Updates with one caption;
- select/fuse translations;
- combine media sets;
- mark anything seen/delivered;
- change publication behavior.

If shadow grouping itself errors, the error is privacy-safe metadata and normal private-review delivery continues unchanged.

Telemetry contains Event/Update IDs, event type, confidence, signal/conflict names, member count and shadow mode. It does not log source bodies, translated captions, URLs, cookies or secrets.

## Source authority and Fanfic boundary

Only enabled configured non-Fanfic sources are eligible for shadow Event membership. An external/unconfigured source is ignored by Event state even if a caller accidentally presents one.

Fanfic/AO3 remains on its independent workflow/state and is not part of Event Fusion.

## Future Long-form / Timeline compatibility

The data model intentionally leaves room for a later additive structure such as:

```text
Event E
  -> Segment 1 -> Update A, Update B
  -> Segment 2 -> Update C
  -> Segment 3 -> Update D, Update E
```

No Segment model, timeline ordering, Live fusion, interview fusion, Going Seventeen fusion, or speech fusion is implemented in this phase.

## Representative benchmark

`data/event_fusion_benchmark.json` contains deterministic cases covering:

1. same event from two sources;
2. complementary details;
3. different wording/language;
4. same interview, different moments;
5. same Live, different moments;
6. unrelated posts close in time;
7. same member, different events;
8. reply/thread relationship;
9. shared repost/original reference;
10. ambiguous wording that stays separate;
11. same concert with different fancams;
12. same performance with different photos;
13. exact duplicate concert media;
14. different concert moments;
15. Going Seventeen same episode/different segments;
16. interview same topic/different answer;
17. translation overlap;
18. complementary source information;
19. unconfigured source blocked;
20. Event membership independent from Update lifecycle.

False-positive grouping is treated as a serious regression.

## Current limitations

- This is deterministic reference/context matching, not general semantic understanding.
- Cross-language posts with no shared reference/context may deliberately remain separate.
- Long-form container identity and individual spoken moments are not modeled yet.
- Existing processing `EventGroup` behavior still drives current captions and delivery.
- Shadow Event groups do not affect Telegram output.
- No claim is made that grouping is perfect; confidence must be measured before delivery semantics are allowed to depend on it.

## Roadmap gate

This foundation must be validated, merged only in a separately authorized merge task, and production-verified in shadow mode first.

Only after that gate should the next isolated phase consider **Long-form/Event Timeline Fusion**.
