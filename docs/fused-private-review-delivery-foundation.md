# Fused Private-Review Delivery Foundation

## Status

This phase adds a **shadow-only, non-authoritative** delivery-planning bridge between
`ForwardReadyPackage` and a future fused Telegram private-review presentation.

It does **not** replace the current Update-oriented send path, does not publish to any
public channel, does not add a Forward button, does not mark receipts, and does not
activate Style/User-Voice authority.

## Runtime position

```text
configured-source retrieval
  -> Event / Timeline / Translation / Style shadows
  -> existing authoritative private-review delivery
  -> ForwardReadyPackage shadow
  -> FusedPrivateReviewDeliveryPlan shadow
  -> privacy-safe metadata observation
```

The current private-review delivery returns successfully before fused planning starts.
Both Forward-ready planning and fused planning have independent fail-open exception
boundaries. A planner failure therefore cannot suppress or reorder the real Telegram
send.

## Identity

`FusedPrivateReviewDeliveryPlan.plan_id` uses the dedicated `fdp:<hash>` namespace.

The hash is reproducible from plan version plus canonical context/update/text/media
references and readiness evidence. It intentionally excludes the
`ForwardReadyPackage.package_id`, Telegram message IDs, receipt IDs, ingestion order,
and all private text bodies.

Each planned unit has its own deterministic `fdu:<hash>` identity.

## Delivery units

The plan models only transport intent:

- `media_album`
- `single_photo`
- `single_video`
- `standalone_media`
- `caption`
- `text`
- `continuation_text`
- `review_controls`

Units carry canonical IDs/fingerprints only. No media bytes or full private bodies are
copied into generic state.

## Text authority planning

Future precedence is represented by references only:

1. confirmed Final Edit, when real confirmed evidence exists;
2. fidelity-safe existing Direct/Channel Style candidate reference;
3. existing faithful-factual reference;
4. current authoritative review Draft fallback.

This phase never turns that preference into production authority.
`user_voice_certified` and `authority_activated` remain false. With insufficient real
edit evidence, no synthetic benchmark or review button action is treated as user voice.

## Media coverage and Telegram grouping

The planner consumes existing `ForwardReadyPackage.media_plan` membership and existing
exact-media identities. It does not regroup Events/Segments and does not redownload or
transcode media.

Distinct useful media remain represented. Exact duplicates are suppressed only when the
existing exact identity already marks them as duplicates. Compatible photo/video media
are grouped in source/package order with a hard maximum of 10 items. Unsupported
album combinations become deterministic standalone media units with an explicit warning.

Concert/performance/appearance coverage therefore keeps distinct fancams, angles,
photos, stage clips and backstage clips instead of using Event/Segment identity as a
dedupe key.

## Caption and long-text planning

Current production media sends have no caption authority, so this phase only records a
future-safe caption attachment strategy.

A short single-text fallback may be represented as a future caption reference. If the
known fallback exceeds the 1024-character caption limit, the plan records
`CAPTION_OVERFLOW_TO_TEXT` and uses text units instead. No body is truncated.

Long text reuses the repository's existing `split_telegram_text` contract. The plan
stores only Draft reference + part indexes/counts; it does not persist the part text.
Future resolution of a reference-only preferred candidate must run the same splitter
before any authority activation.

## Event and Segment behavior

The fused planner consumes package membership; it never creates another semantic
grouping engine.

- same Segment can become one package plan;
- separate Segments under the same Event stay separate plans;
- Live/GOSE/interview/fansign boundaries therefore remain whatever Event Timeline
  already proved;
- nearby time, same member/show/performance, or similar wording are never new merge
  evidence here.

## Readiness

Fused readiness is derived from existing Forward-ready readiness:

- `READY_TO_PRESENT`
- `READY_WITH_WARNINGS`
- `NEEDS_REVIEW`
- `BLOCKED`
- `MEDIA_INCOMPLETE`
- `PARTIAL_COVERAGE`

Translation conflicts and fidelity blocks map to `BLOCKED`. Partial retrieval is
explicitly surfaced as `PARTIAL_COVERAGE`. Media failure cannot be called fully ready.

`PLANNED` never means `DELIVERED`.

## Receipt and retry authority

`MessageDeliveryStore` remains text receipt authority.
`MediaDeliveryLedger` remains exact-media receipt authority.

The planner never calls either store's confirmation/mark-delivered methods. Telegram
exactly-once delivery is not claimed.

Plans are deterministic and recomputable after restart. This foundation deliberately
does **not** persist a second generic fused-plan state; malformed/stale plan-like
metadata is ignored and a fresh plan is derived from canonical Forward-ready evidence.

## Review controls and future Forward contract

The last review-control unit references the existing control family:

- Copy
- Reject
- Funnier
- Softer
- Precise
- Final Edit

No callback payloads or second review system are created.

A future Forward contract is modeled only as disabled metadata. It requires explicit
user action, is package-specific/private-review-controlled, has no target chat
configured, is never automatic, and is never public by default.

## Privacy boundary

`internal_review_metadata` may contain package/Event/Segment/Update references and
readiness codes.

`forwardable_content` is intentionally a body-free summary and contains no debug IDs,
provider errors, source-health metadata, fingerprints, or secret-like fields.

Runtime observations contain only bounded metadata such as plan/package ID, counts,
readiness, warning count, version, and shadow mode. Final-edit bodies, full captions,
tokens, cookies, authorization headers, and source URLs are never logged by this layer.

## Validation

The phase includes:

- 50 independently named difficult scenario regressions plus structural safety tests;
- a 50-case deterministic fused-delivery benchmark;
- hard gates for false merges, distinct-media loss, unsupported factual additions,
  receipt-authority violations, public-publishing actions, chronology errors,
  readiness misclassification, and Telegram-limit violations;
- the existing full unit-test suite and all earlier foundation benchmarks in natural PR
  CI.

## Safety invariants

Unchanged by this phase:

- `review_only = true`
- configured-source-only non-Fanfic retrieval
- 24 configured sources
- Zero-Silent-Miss / recovery / scheduling architecture
- Event / Timeline / Translation / Style / Final Edit / Forward-ready authority boundaries
- `AUTO_LEARN = false`
- no fabricated real user edit triplets
- `REALTIME_SHADOW_MODE` defaults off
- Fanfic/AO3 remains independent
- no paid infrastructure
- no public auto-publish
