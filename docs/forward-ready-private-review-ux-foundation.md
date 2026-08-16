# Forward-ready private-review UX foundation

## Scope and safety

This phase adds a deterministic `ForwardReadyPackage` plan in `shadow` mode. It
does not change retrieval, organizer grouping, captions, Telegram sends, review
buttons, seen/delivered state, receipts, completeness, or public-channel behavior.
There is no Forward button and no publishing target in this foundation.

The package is recomputable presentation metadata. Its `frp:` identity is in a
dedicated namespace and is never substituted for Update, Event, Segment,
translation, media, delivery-key, or receipt identity.

## Audited production path

The current Daily path is:

1. configured-source retrieval produces canonical `Update` objects;
2. shadow Event Fusion and Timeline attach Event/Segment metadata;
3. shadow Translation Fusion evaluates factual Persian and conflicts;
4. shadow Channel Style Rewrite consumes Direct User Style Rules and calibration
   metadata without gaining authority;
5. the existing writer creates one authoritative `Draft` per Update;
6. private review sends each Update's media first, then its Draft text and existing
   fun/soft/precise/copy/reject/final-edit controls;
7. Telegram text parts and exact-media deliveries retain their separate SQLite
   receipt owners;
8. confirmed final-edit bodies remain exclusively in private-review SQLite.

Today, albums are sent in Telegram groups of at most 10 and logical text over
Telegram's 4096-character limit is split, with review controls on the final text
part. Media carries no duplicated caption; text follows it. This is safe but can
require forwarding multiple Telegram messages. The package model describes a
cleaner future presentation without activating it.

## Package model

Each package contains reference-only plans:

- package identity and context kind;
- Event and Segment references;
- canonical Update references in chronological order;
- `ForwardReadyTextPlan` and `ForwardReadyMediaPlan`;
- Telegram presentation order and limit metadata;
- internal review metadata separated from forwardable references;
- warnings and one concise readiness indicator.

Persisted metadata is bounded under the existing Event Fusion state namespace.
It includes IDs, fingerprints, ordering, status, and warnings only. It never
duplicates draft/final-edit/translation bodies, source URLs, media bytes, secrets,
or receipt rows.

## Membership and long-form safety

An Update without existing Segment evidence receives a standalone package. Multiple
Updates are placed together only when existing Timeline metadata puts them in the
same Segment. The planner does not infer a new Event, merge nearby posts, or merge
all moments from the same Live, GOSE episode, interview, fansign, or concert.

This deliberately prefers false splits to false merges. A future safe sequence of
related Segments can be modeled only after explicit Timeline evidence exists; this
foundation does not guess it.

## Text plan and future preference

The text plan references the current authoritative Draft, faithful factual
fingerprint, accepted Channel/Direct Style fingerprint, and a genuinely confirmed
final-edit ID/fingerprint when present. No private final-edit body is copied.

The recorded future preference is:

1. genuinely confirmed final edit;
2. fidelity-safe accepted Direct/Channel Style shadow candidate;
3. faithful factual candidate.

`current_authority` remains `authoritative_review_draft` and
`authority_activated` remains false. Existing Direct User Style formatting is
consumed from its output metadata and is not reimplemented here.

## Media plan and coverage

Media references reuse the exact URL identity already used by the Telegram media
delivery ledger. Ordering is deterministic: Update chronology, then source media
order. Telegram batches are planned at 10 items and remain media-first.

All distinct concert/performance/public-event photos, fancams, stage clips,
backstage clips, interactions, official footage, press footage, and fan footage
remain represented, including media-only coverage with no speech. Same Event,
Segment, or performance never implies deduplication. Repeated exact identities are
marked `existing_exact_dedupe`; the existing receipt ledger remains authoritative.

## Readiness

- `READY`: forwardable text plan with no known warning.
- `READY_WITH_WARNINGS`: valid plan with partial coverage or Telegram split warning.
- `NEEDS_REVIEW`: no sufficiently prepared forwardable plan.
- `BLOCKED_FIDELITY`: Translation Fusion is not fidelity-ready.
- `BLOCKED_CONFLICT`: factual conflict remains unresolved.
- `READY_TEXT_MEDIA_INCOMPLETE`: text is usable but media preparation failed/partial.
- `MEDIA_ONLY`: useful media coverage exists without required text.

Partial retrieval keeps every valid discovered Update and adds `PARTIAL_COVERAGE`;
it never claims complete coverage. Conflict wins over style acceptance. Media
failure cannot produce `READY`.

## Internal versus forwardable boundary

Internal metadata contains source Update, Event, Segment, warning, and readiness
evidence for review. Forwardable content contains only text/media references and
explicitly excludes technical metadata. The planner creates no debug message and
adds no button, dashboard, callback, Telegram call, forward action, or public target.

## Reversibility and rollback

Plans are regenerated from canonical state. Reclassifying an Update simply changes
the next package membership/identity; it never mutates the Update or lifecycle.

Rollback is removal of the forward-ready runtime call, compatibility hook, planner,
benchmark, tests, and this document. Existing delivery and SQLite schemas require
no rollback or migration; unknown forward-ready metadata is discarded by the
existing sanitizer when the extension is absent.

## Known limitations

- This phase evaluates plans but does not change the current multi-message Telegram
  presentation, so actual one-action forwarding is not yet production-observed.
- It does not combine separate Segments into a sequence.
- Preparation status uses existing lifecycle evidence; it does not download or
  transcode media.
- Exact byte-level/Telegram-file duplicate knowledge remains in the delivery ledger;
  the package can mark URL-exact duplicates without becoming receipt authority.
- No style, fused-delivery, automatic-learning, or publishing authority is enabled.
