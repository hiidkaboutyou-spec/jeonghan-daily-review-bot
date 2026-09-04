# Phase 1 — Raw Observation Store

Status: **IMPLEMENTED IN SHADOW / COMPATIBILITY MODE**

This phase creates durable pre-filter source truth for configured X accounts. It is the first implementation step after the Phase 0 Production Truth Audit.

The production editorial behavior is intentionally **not** switched yet. Existing configured-source filtering, event grouping, translation, media and Telegram delivery remain authoritative while the raw layer records what the provider actually exposed.

## Goal

The Phase 1 invariant is:

> Persist every configured-source observation before relevance, source-mode keyword filtering, editorial dedupe, grouping, translation or delivery can remove it from the working set.

A post may later be classified hidden or irrelevant. It must not disappear merely because a keyword rule did not recognize it.

## Capture boundary

The runtime wraps `XCollector._convert_tweet`.

This is deliberately below the current source-mode gate. In the production configured-source completeness collector, the provider tweet is converted before later policy such as retweet exclusion and before `_configured_only()` applies `SourceModeGate.filter_posts()`.

Therefore Phase 1 can preserve evidence for cases such as:

- media-only configured-source posts;
- generic captions;
- thread/reply continuations without a Jeonghan keyword;
- unfamiliar nicknames;
- posts that the current `keyword_filter` rejects;
- retweets that the current editorial timeline later excludes;
- provider rows that could not be converted into a normal `Update`.

Only enabled configured X sources enter this source-truth store. Global/external search authors do not become trusted configured-source observations.

## Durable contract

Schema version: `1`.

The canonical table stores:

- provider;
- external post ID;
- configured source handle;
- source mode at observation time;
- source timestamp;
- original text;
- conversation/reply/quote relationships;
- quoted text/author;
- language;
- media and quoted-media references;
- post type;
- retweet/reply/quote/media-only flags;
- retrieval provenance;
- retrieval attempt ID when available;
- bounded hash of public provider-visible fields;
- conversion status;
- first/last observed timestamps;
- observation count;
- current snapshot hash.

A second immutable version table stores each distinct snapshot once.

This means a five-minute polling loop does not duplicate the full same post forever, while provider-visible edits or representation changes remain auditable.

## Storage

The store uses SQLite and, by default, shares the existing durable:

`.state/private-review.sqlite3`

The production workflow already restores/checkpoints/caches this database, so Phase 1 does not add a second state persistence mechanism or require a workflow cache change.

`RAW_OBSERVATION_DB_PATH` exists only as an explicit test/operator override.

## Failure semantics

Raw observation persistence is correctness state, not best-effort telemetry.

If a configured-source observation reaches the conversion boundary but SQLite cannot persist it, collection fails rather than silently continuing into destructive relevance/filter logic.

This is intentional fail-closed behavior.

No secrets/cookies/session payloads are serialized into the raw observation store. The provider payload hash is built from a bounded allowlist of public tweet fields only.

## Compatibility behavior

Phase 1 does **not** yet:

- change `keyword_filter` delivery behavior;
- expose hidden posts in Telegram;
- replace the global scheduled cursor;
- create the source ledger;
- change source ordering;
- make raw observations delivery-authoritative;
- add Rust;
- change public posting behavior.

Those are later phases.

The current pipeline can therefore be compared against the new raw truth without a big-bang migration.

## Tests

Phase 1 adds regression coverage proving:

1. canonical original fields persist;
2. identical repeated observations increment count without creating duplicate versions;
3. changed snapshots keep immutable version history;
4. incomplete identity fails closed;
5. a generic `keyword_filter` post is persistable before the current gate rejects it;
6. a media-only `keyword_filter` post is preserved before that gate;
7. retweets are represented in raw truth;
8. external/nonconfigured authors cannot enter configured-source truth;
9. conversion failures still produce minimal source observations where provider identity is available;
10. the runtime capture hook is installed.

## Exit gate

Phase 1 is complete when CI proves that configured-source posts which would be lost by current relevance/source-mode policy can still be found in the durable raw observation store.

This phase establishes **observation truth** only.

Phase 2 can now build a per-source ledger on top of a durable input instead of trying to infer truth from downstream delivered candidates.

## Rollback

Rollback is simple:

- remove the `raw_observation_runtime` package import;
- existing production filtering/delivery behavior remains unchanged;
- the additive `raw_observations`, `raw_observation_versions` and `raw_observation_meta` SQLite tables may remain harmlessly unused.

No destructive migration of existing archive/draft/review tables is required.
