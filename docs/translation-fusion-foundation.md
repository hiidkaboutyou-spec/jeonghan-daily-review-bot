# Translation Selection / Fusion + Fidelity Foundation

Status: **development / shadow-only**  
Roadmap phase: **TRANSLATION SELECTION / FUSION + FIDELITY FOUNDATION**

This phase adds the smallest safe evidence layer between production-verified Event/Timeline understanding and the existing private-review translation/delivery pipeline. It does **not** implement final channel-style rewriting or fused Telegram delivery.

## Authoritative pipeline boundary

The current production order remains:

configured source Updates
→ Event Fusion shadow
→ Timeline Segment shadow
→ Translation Fusion/Fidelity shadow
→ existing `ChannelStyleCaptionWriter`
→ existing per-Update Draft creation
→ existing private-review Telegram delivery

Translation Fusion never becomes a retrieval, lifecycle, seen/delivered, media, Telegram-receipt, Event-membership, Segment-membership, or public-publishing authority.

If Translation Fusion raises an exception, the wrapper records a bounded warning and returns the upstream Event result. Existing private-review processing continues.

## Existing translation architecture audited

The repository already has one translation pipeline. This phase deliberately reuses its factual contracts instead of creating a second delivery pipeline.

Existing pieces include:

- `ChannelStyleCaptionWriter` with SOURCE as factual authority.
- A legacy neutral fidelity pass and style-transfer pass.
- Production v2 direct translation with an optional Gemini client.
- Deterministic hard-fact verification for names/identities, semantic numbers/dates/times, URLs, hashtags, dialogue/speaker structure and quotes.
- `translation_safety` manual-review gates and outage behavior.
- Existing retry/draft/lifecycle logic in `PrivateReviewApplication`.
- Historical channel examples as **style authority only**, never factual authority.

The existing channel-style corpus is untouched. Translation Fusion never imports StyleMemory and never uses historical channel examples to authorize a fact.

## Evidence model

`TranslationEvidence` is always traceable to one canonical `Update.id`.

Ephemeral/in-memory evidence can include:

- Update ID
- configured source handle
- source language
- evidence kind
- original/current Update translation source
- an already available Persian candidate when one is genuinely present
- Event ID and Segment ID
- Timeline relationship and confidence
- matching signals/conflicts
- chronology index
- bounded media reference IDs

Durable state intentionally excludes full source bodies, full translations, full captions and media URLs. It stores bounded metadata and content hashes only. Canonical Update/archive evidence remains the owner of the source body.

Evidence kinds distinguish:

- direct labelled translation
- Persian source
- original-language evidence
- mixed-language evidence
- fan translation/description
- summary/paraphrase
- unknown evidence

An English fan translation is not assumed to be more authoritative than Korean or Japanese original-language evidence merely because it is English.

## Provider-neutral behavior

This foundation does not add a model API.

Selection, conflict detection, evidence accounting, fidelity gates, persistence and the benchmark are deterministic.

A real runtime `fused_factual_text` is emitted only when a faithful Persian candidate already exists in configured-source evidence. When the evidence is original-language/English-only and no trustworthy Persian candidate exists, the shadow result becomes `needs_translation`/`review_required` rather than fabricating Persian.

That limitation is intentional. A future isolated integration may let the existing translation infrastructure translate the selected factual backbone, but this phase does not introduce a parallel model pipeline and does not consume extra Gemini quota.

## Backbone selection

Backbone selection is factual, deterministic and explainable.

Signals include:

1. evidence kind: direct translation > Persian source > original-language evidence > mixed-language evidence > fan translation/description > summary/paraphrase;
2. Timeline relationship quality;
3. configured evidence strength;
4. direct quoted material;
5. deterministic candidate fidelity, when a Persian candidate exists.

The following are **not** selection signals:

- source popularity
- follower count
- prettiness
- channel-style similarity
- historical style examples

Ties are stable on `Update.id`.

## Complementary fusion

Only evidence explicitly related as:

- `complementary`
- `continuation`

may add a second factual Persian passage automatically.

`same_moment` alternatives participate in backbone/conflict analysis but are not automatically appended merely because they describe the same moment. This prevents two equivalent translations from turning into duplicated text.

Before a complementary candidate is added:

- its own source→candidate fidelity gates must pass;
- no pair conflict may be present;
- it must not be a duplicate of already selected text.

Every emitted line therefore comes from an attributable configured-source evidence item. No speculation, fan interpretation, emotional inference or invented relationship context is permitted.

## Conflict handling

Conflicts are never silently averaged.

High-confidence conflict signals include:

- Timeline `conflicting`
- incompatible number/date/time facts
- incompatible speaker evidence
- high-overlap negation disagreement
- Timeline-provided conflict metadata

Conflicts remain represented in:

- `conflict_update_ids`
- `unresolved_conflicts`
- `review_required`
- bounded decision metadata

Statuses are conservative:

- `faithful_shadow_candidate`
- `needs_translation`
- `needs_review`
- `insufficient_evidence`

The current phase does not choose a final answer when evidence remains unresolved.

## Original-language fidelity gates

The foundation reuses the production hard-fact verifier and adds conservative checks for:

- names/identities
- numbers
- dates/times
- speaker attribution
- question vs statement form
- negation
- uncertainty/modality
- quoted vs paraphrased distinction
- unsupported romantic/relationship inference
- unsupported causal links

Original-language evidence is retained even when no Persian candidate is available. Lack of original-language evidence lowers the evidence basis; it is never silently treated as equivalent to direct original evidence.

## Timeline relationship rules

Translation Fusion consumes existing Timeline relationships:

- `same_moment`: compare/select; do not automatically append.
- `complementary`: may add after fidelity/conflict checks.
- `continuation`: may add after fidelity/conflict checks.
- `conflicting`: preserve explicitly; require review.
- `ambiguous`: never auto-fuse.
- `separate`: never auto-fuse.

Event/Segment membership itself is not modified.

## Output model

`TranslationFusionResult` contains:

- `event_id`
- `segment_id`
- `evidence_update_ids`
- `backbone_update_id`
- `complementary_update_ids`
- `conflict_update_ids`
- ephemeral `fused_factual_text`
- `source_languages`
- `fidelity_status`
- `confidence`
- `unresolved_conflicts`
- `review_required`
- bounded reasoning/signals metadata
- withheld Update IDs
- deterministic fingerprint

No private chain-of-thought is stored.

Durable serialization omits `fused_factual_text` and stores only a hash/presence bit plus bounded outcome metadata.

## Durable state

No database or top-level schema migration is added.

Translation Fusion extends the existing `event_fusion` namespace with:

- `translation_fusion_version`
- `translation_fusion_mode`
- `translation_evidence`
- `translation_fusion_results`
- `translation_fusion_decisions`

The existing Event/Timeline sanitizer/pruner is wrapped so the new metadata is bounded and restart-safe.

It does not duplicate:

- Update bodies
- Persian translations
- captions
- media URLs
- cookies/tokens/secrets

Translation Fusion fingerprints are analysis identities only. They do not replace existing Update, Event, Segment, translation job, media or Telegram delivery identities.

## Concert invariant

Concerts remain coverage-first.

Translation Fusion may analyze two translations of the same spoken ment, but:

- Event ID is not a media dedupe key.
- Segment ID is not a media dedupe key.
- Translation Fusion fingerprint is not a media dedupe key.
- distinct fancams remain distinct;
- distinct videos remain distinct;
- distinct photos remain distinct;
- distinct performances/interactions/backstage content remain distinct.

Only the pre-existing exact-media identity rules may identify exact media duplicates.

## Deterministic benchmark

`data/translation_fusion_benchmark.json` contains 30 synthetic difficult cases covering:

1. equivalent same-moment translations
2. complete vs partial
3. complementary details
4. direct quote vs summary
5. Korean original + English translation
6. Japanese original + English translation
7. mixed language
8. name preservation
9. number preservation
10. date/time preservation
11. speaker attribution
12. negation
13. question form
14. uncertainty/modality
15. conflicting number
16. conflicting speaker
17. conflicting negation
18. ambiguous pronoun
19. wordplay uncertainty
20. paraphrase vs direct translation
21. unsupported fan interpretation
22. unrelated commentary
23. continuation
24. same-moment overlap
25. ambiguous Timeline relation
26. separate Segment relation
27. concert ment translations
28. exact duplicated source
29. missing original-language evidence
30. unresolved conflict requiring review

The runner reports separate metrics:

- factual precision
- factual recall
- unsupported-addition count
- contradiction preservation
- speaker-attribution accuracy
- name accuracy
- number/date accuracy
- negation accuracy
- review-required correctness

Hard gate: **unsupported factual additions = 0**.

The deterministic benchmark performs no model call. An unavailable model quota can therefore never be mislabeled as a passing model-quality benchmark.

## Explicitly out of scope

This phase does not implement or modify:

- final channel-style rewriting
- final Persian voice matching
- fused Telegram delivery
- media quality redesign
- Forward-ready UX
- Fast Detector
- Go/Rust
- paid LLMs/embeddings/translation APIs
- vector databases
- Supabase
- Redis/Celery/queues
- paid hosting
- paid X API
- Fanfic/AO3 retrieval

Fanfic/AO3 remains independent.
