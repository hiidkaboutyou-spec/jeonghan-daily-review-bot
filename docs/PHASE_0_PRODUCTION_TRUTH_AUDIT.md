# Phase 0 — Production Truth Audit

Status: **AUDIT COMPLETE**

This audit establishes what the current production bot can actually prove today. It does **not** declare the current product North-Star complete or trustworthy. No runtime code, credentials, production workflow behavior, source configuration, or public posting behavior was changed as part of this audit.

## Audit baseline

Production code audited against `main` commit:

- `f7245a57b5e3f00173576eb60af680ab6e30edbd`
- commit title: `feat: Structured Production Outcome Contract (#47)`

Live evidence was also checked against GitHub Actions run `33904826788` (run #3301), which executed the production monitor on this same commit and uploaded a `production-outcome` artifact.

## Executive verdict

The current bot has a number of strong safety foundations, especially configured-source authority, bounded timeline completeness checks, delivery resume/idempotency, and media delivery deduplication.

However, it does **not** yet satisfy the new product North Star.

The most important result of Phase 0 is:

> A successful/HEALTHY production run is not currently proof that every configured source was completely observed, and the current pipeline can still silently discard real source posts before durable persistence.

Architecture work may proceed only with these gaps treated as explicit migration requirements.

## Current source inventory

`config/sources.json` currently contains:

- 24 configured X sources;
- 23 enabled sources;
- 1 disabled source (`flamehanie`, recorded as suspended on X);
- 11 enabled `full_feed` sources;
- 12 enabled `keyword_filter` sources;
- `include_replies=true` for all configured sources.

The configured list is authoritative for normal non-Fanfic X retrieval.

## Actual current data flow

The effective main path is approximately:

```text
configured X sources
  -> per-source X timeline retrieval
  -> optional author-scoped recovery search after timeline failure
  -> configured-author filter
  -> source-mode gate (full_feed / keyword_filter)
  -> post-ID dedupe
  -> global chronological ordering
  -> event/category organizer
  -> AI writer / translation-caption path
  -> theme renderer
  -> media preparation + Telegram delivery
  -> draft/review inbox
  -> seen/archive/state persistence
```

This is not yet the desired target:

```text
source retrieval
  -> persist every raw observation
  -> source ledger + completeness proof
  -> reversible relevance classification
  -> source-first editorial queue
  -> independent media/original/translation/caption artifacts
```

## Source collection findings

### Configured-source authority — PASS

`source_authority_hardening.py` restricts normal non-Fanfic X collection, search, event recovery and delivery to configured authors. Author-scoped recovery does not widen the source set.

This is worth preserving.

### Timeline completeness boundary — PASS with bounded-limit caveat

`x_completeness.py` correctly refuses to call a bounded source window complete when the timeline result limit is reached before the collector crosses the requested lower time boundary. It raises `XCompletenessError` instead.

This is a strong existing invariant and should survive the migration.

Caveat: scheduled windows use a finite safety budget. A very high-volume source can therefore become explicitly PARTIAL/UNPROVEN. That is safe behavior, but the future ledger must expose the reason source-by-source.

### Replies — PASS for configured policy

All configured source rows request replies, and the completeness collector uses the tweets-and-replies timeline when `include_replies=true`.

### Retweets — PRODUCT/POLICY BLOCKER

The current completeness timeline explicitly skips retweets.

Therefore a current `COMPLETE` source timeline really means complete for the retained non-retweet policy, not necessarily complete for every activity visible on that account.

For the new `persist first, classify later` architecture, retweets should be observed/persisted and classified unless the admin explicitly defines retweets as out of scope. They must not disappear merely as an implementation side effect.

## Silent-drop findings

### Raw observation before filtering — BLOCKER

There is no durable canonical raw-observation layer before relevance/source-mode filtering.

`StateStore.archive` and the SQLite archive are useful retained-item archives, but they receive items only after earlier collection/filtering decisions. They cannot recover a source post that was already eliminated before queue/delivery.

### `keyword_filter` source mode — BLOCKER

Twelve enabled sources use `keyword_filter`.

The mode gate decides acceptance using `text + quoted_text` and configured Jeonghan terms/angel markers. It can therefore drop a true relevant source post when, for example:

- the post is media-only;
- the caption is generic;
- a thread continuation omits Jeonghan's name;
- a new/unfamiliar nickname is used;
- a relevant photo/video has weak textual signals.

Because the gate is applied before a canonical raw observation is durably persisted, these are true silent-drop paths under the new North Star.

### Existing `zero_silent_miss` instrumentation — PARTIAL

The current observability layer is valuable downstream. It tracks lifecycle status for returned/queued items and quarantines malformed pending rows rather than losing them.

But it observes counts around the configured/source-mode filter; it does not persist the actual objects rejected by that gate. Therefore its existence does not mean the new no-silent-drop requirement is satisfied.

## Dedupe findings

### Retained-item post-ID dedupe — PASS

Configured-author results are deduplicated deterministically by post ID, preferring the richer representation when duplicates are observed.

### Dedupe before raw persistence — MIGRATION GAP

For the target architecture, duplicate observations should still have retrieval/provenance evidence before canonical post-level collapse. The existing dedupe is appropriate for editorial candidates, but should not serve as the only observation record.

## Cursor and retry findings

### Scheduled cursor — BLOCKER

The current scheduled monitor uses one global `last_auto_run` cursor.

If one source is partial, the runtime correctly avoids advancing that global cursor. This is safer than losing content, but it means the cursor is not source-specific.

Consequences:

- one bad source pins progress for all sources;
- retry is primarily global-window retry rather than a durable per-source retry contract;
- production cannot independently answer `where is source A's cursor versus source B's cursor?`.

The target source ledger requires a cursor/outcome per configured source/window.

### Manual configured-source replay — useful but not equivalent

A configured-source complete-window fetch exists for manual 24h/source operations. This is useful infrastructure, but it is not the same as a persistent per-source scheduled cursor and retry ledger.

## Ordering and editorial UX findings

### Main presentation order — BLOCKER

`organize_updates()` groups updates into event/conversation groups and then sorts groups by `started_at`.

`PrivateReviewApplication.deliver_updates()` explicitly presents those groups oldest-to-newest.

Therefore the current product remains event/time-first. It does not finish source A before beginning source B.

### Review inbox — PARTIAL

The SQLite review inbox is draft-centric and supports pending/ready/rejected plus source/category metadata. It is a useful base.

It does not yet model the target source-level review session with:

- source progress;
- COMPLETE/PARTIAL/UNPROVEN badge;
- raw/relevant/uncertain/hidden counts;
- source-specific retry/defer;
- `Show hidden`;
- source-first `Next` semantics.

## Relevance findings

### Current model — BLOCKER

The current source-mode gate is effectively destructive acceptance filtering for `keyword_filter` sources.

The target model must instead persist first and then assign a reversible state:

- `RELEVANT`
- `UNCERTAIN`
- `NOT_RELEVANT`

`NOT_RELEVANT` must remain countable, auditable and revealable.

## Original / translation / caption findings

### Original update representation — PASS foundation

`Update` preserves useful original fields including source text, quoted text, reply/conversation metadata and media references.

### Independent artifact lifecycle — PARTIAL

The archive schema already has separate `text`, `translated_text`, and `caption` columns, and translation-fusion code stores substantial evidence/fidelity metadata.

However, the production editorial lifecycle is still largely draft/event oriented. The target requires explicit independent per-post artifacts/states such as:

- original ready;
- translation pending/ready/fallback;
- caption pending/ready;
- needs review.

A `Retranslate` action must not recollect media/source, and a `Rewrite` must not imply translation failure.

## Media findings

### Delivery reliability and exact-media dedupe — STRONG FOUNDATION

The current media layer already has useful production-grade behavior:

- Telegram cached-file fast path;
- source-URL identity;
- downloaded-byte SHA-256 identity;
- Telegram `file_unique_id` identity;
- persistent delivery receipts;
- repeat suppression;
- content fallback preparation;
- text delivery can survive media unavailability.

These behaviors should be preserved.

### Asset-state contract — PARTIAL

The target still needs a first-class per-asset state/provenance/retry contract rather than relying mainly on delivery/cache behavior.

## Delivery and durability findings

### Telegram retry/resume — PASS foundation

The current private delivery path persists group draft plans before network delivery and checkpoints after acknowledged items. Pending delivery, seen state and message/media receipt mechanisms provide a strong base for idempotent resume.

### Durable archives — PARTIAL relative to North Star

The JSON and SQLite archives are valuable, but they are archives of retained pipeline items, not a guaranteed pre-classification observation ledger.

## Production health/outcome findings

### Workflow `success` is not collection truth — BLOCKER

The production GitHub Actions workflow can complete successfully when the Python process exits successfully. The structured Production Outcome was intended to provide a stronger truth contract, which is the right direction.

### Production Outcome source accounting — BLOCKER

`production_outcome_runtime.py` currently infers source completion by looping through enabled configured sources after the scheduled scan and treating a source as complete when its handle is not found in `collector.last_errors`.

That is not equivalent to consuming explicit per-source completion evidence.

It can also misrepresent an early-return/not-due run because source accounting is not tied to a durable source-attempt ledger.

### Live artifact proves the contract can report false confidence

The audited live production run `33904826788`:

- ran against the audited `main` SHA;
- completed its live-monitor step successfully;
- uploaded a `production-outcome` artifact;
- artifact classified the run as `healthy`;
- but the same artifact contained:
  - `configured_source_count = 0`;
  - `active_source_count = 0`;
  - `attempted_source_count = 0`;
  - `complete_source_count = 0`;
  - `collection_complete = false`;
  - `cursor_advanced = false`;
  - empty `cursor_reason`;
  - `useful_work_performed = true`.

Therefore `HEALTHY` is currently **not** evidence that source collection was complete. This is a direct live-production observation, not merely static-code inference.

### Discovery accounting — PARTIAL/BROKEN CONTRACT

The production outcome discovery hook calculates approximate queue-time values but does not currently wire those values into the outcome builder's discovery counters. Zero discovery fields can therefore mean `not measured`, not necessarily `nothing discovered`.

## Watchdog finding

The watchdog is correctly designed to prefer the structured outcome over workflow conclusion when it can retrieve and validate the artifact.

However, because the outcome itself does not yet contain reliable source truth, downstream recovery decisions cannot become trustworthy merely by consuming that artifact. Source ledger evidence has to become the upstream contract first.

The artifact download code should also remain under regression coverage because GitHub artifact downloads use redirects and are operationally distinct from ordinary GitHub JSON API calls.

## Gap matrix

| Area | Phase 0 verdict | Reason |
|---|---|---|
| Configured-source authority | PASS | external authors blocked from normal X pipeline |
| Source inventory | PASS | explicit config exists |
| Timeline lower-bound completeness | PASS | capped timeline fails closed |
| Reply retrieval | PASS | all configured sources currently include replies |
| Retweets | BLOCKER / policy gap | skipped before target persistence |
| Raw observation persistence | BLOCKER | no pre-filter canonical store |
| `keyword_filter` behavior | BLOCKER | real source posts can vanish before persistence |
| Retained post-ID dedupe | PASS | deterministic and richer-copy preserving |
| Global scheduled cursor safety | PASS as legacy safety | partial scan holds cursor |
| Per-source cursor | BLOCKER | does not exist |
| Per-source retry ledger | BLOCKER/PARTIAL | no durable scheduled source ledger |
| Source-first ordering | BLOCKER | event/time-first presentation |
| Reversible relevance states | BLOCKER | destructive filtering instead |
| JSON/SQLite archive | PARTIAL | useful, but too late in pipeline |
| Media delivery/dedupe | STRONG FOUNDATION | robust delivery identities and resume behavior |
| Media asset lifecycle | PARTIAL | delivery-centric, not full asset-state contract |
| Original representation | PASS foundation | Update preserves useful source context |
| Translation/caption separation | PARTIAL | data exists, lifecycle not fully independent |
| Review inbox | PARTIAL | draft-centric, not source-ledger UX |
| Delivery durability/idempotency | PASS foundation | persistent drafts/queue/receipts |
| Production Outcome source truth | BLOCKER | current `HEALTHY` is not completeness proof |
| Discovery outcome counters | PARTIAL/BROKEN | not fully wired |
| Workflow success as health | NOT ACCEPTABLE AS PROOF | process success != source completeness |

## False assumptions Phase 0 explicitly rejects

1. `GitHub Actions success` does not mean every source was complete.
2. `HEALTHY` in the current outcome does not mean source collection was complete.
3. A module named `zero_silent_miss` does not mean no source post can be silently lost before persistence.
4. Having an archive does not mean there is a raw-observation store.
5. Having timeline completeness logic does not mean there is a per-source cursor.
6. Having a review inbox does not mean the UX is source-first.
7. Having translation metadata does not yet mean original/translation/caption are independent editorial artifacts.

## Migration dependency map

The safe dependency order after this audit is:

```text
raw observation contract/store
  -> source/window ledger
  -> per-source completeness + cursor rules
  -> reversible relevance classification
  -> source-first queue
  -> independent media/translation/caption artifacts
  -> personalized voice/theme layers
```

Do **not** begin with a large Rust rewrite.

Rust should enter only after the first versioned observation/source-ledger contracts are explicit enough to test across the Python boundary.

## Phase 1 entry requirements

Phase 1 should create `RawObservation v1` and a shadow persistence path without replacing legacy delivery yet.

Minimum requirements:

- every observed configured-source post is persisted before `full_feed/keyword_filter` relevance decisions;
- source handle/source ID and provider are explicit;
- retrieval attempt ID and requested window are explicit;
- post ID, timestamps, original text, quote/reply context and media references are preserved;
- duplicate observations retain provenance while canonical post identity remains deterministic;
- retweet behavior becomes an explicit product policy/state rather than an invisible hard skip;
- failed/partial source observations remain tied to the source attempt;
- legacy delivery remains behind the existing compatible path while shadow data is compared;
- no public auto-publishing is introduced.

## Phase 0 exit gate

**Met.**

We can now answer, with code and live-production evidence, where the existing system is strong and where it cannot prove the new product invariants.

Phase 1 may begin, but only as an incremental raw-observation/shadow-storage change. The production bot should not yet be described as source-complete, no-silent-drop, or source-first.
