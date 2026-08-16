# Channel-Style Rewrite / User Voice Foundation

## Scope

This phase is text-only and shadow-only. Existing private-review Telegram output remains authoritative. The layer never owns retrieval, Event/Timeline grouping, Translation Fusion, media, seen/delivered lifecycle, Telegram receipts, or public publishing.

The production invariant is:

`configured evidence -> Translation Fusion / fidelity -> faithful factual Persian -> shadow style evaluation -> existing private review`

Historical channel examples are style demonstrations only. They are never factual evidence for the current update.

Configured Direct User Style Rules now form a deterministic presentation plan after factual fusion and before historical/provider styling. Their headers, prefixes, and rotating symbols remain shadow-only and are validated through factual projection; see `docs/direct-user-style-rules-foundation.md`.

## Existing system reused

The phase preserves the 16,306-example historical channel corpus, its 10 stable-ID shards, the existing `ChannelStyleMemory` SQLite/FTS index, channel profile/glossary, Persian/channel quality helpers, and existing RTL/RLM presentation code. No historical example is deleted or rewritten.

The old production `ChannelStyleCaptionWriter` remains the existing private-review writer. This foundation does not replace its Telegram output. Instead, it creates a separate shadow evaluation boundary after the already-shadow Translation Fusion layer.

## Provider-neutral contract

`StyleRewriteInput` contains only:

- faithful factual Persian text;
- Event and Segment references;
- content type;
- speaker metadata;
- deterministic factual invariants;
- style-profile key;
- bounded selected historical example IDs.

Historical example bodies are passed separately to a style provider as demonstrations and never enter the factual-invariant ledger.

The initial provider is `ConservativeLocalStyleProvider`. It is deterministic, local, free, and intentionally limited to Persian surface normalization. Richer future providers can implement the same protocol, but every candidate must pass the same factual lock.

## Example retrieval

The new shadow retrieval path is bounded to at most five examples. It does not query FTS by the current topic/text. Ranking uses form signals only:

- exact content type;
- structural family;
- dialogue structure;
- length similarity;
- target register;
- format diversity.

Historical date/recency contributes zero, matching the existing corpus authority policy. A different content family is rejected. This avoids selecting an old post simply because it mentions the same place/person/topic.

## Content profiles

Profiles are measured from the real corpus, not handwritten personas. Runtime profiles are derived from actual examples for a content type and are enabled only when at least 12 historical examples exist.

The benchmark also audits requested broader groups (Live, Going Seventeen, variety/reality, interview, official, photo/video, fansign/video call, concert ment, social/casual, brand/event). A broad group is marked supported only when real corpus evidence reaches the minimum. Unsupported groups do not get invented style rules.

Measured profile fields include example count, median length, multiline rate, dialogue rate, emoji rate, reaction rate, formal-connector rate, register, and evidence-derived intensity.

## Factual lock

The candidate is compared to the faithful factual Persian input, never to historical examples as factual authority. Deterministic gates cover:

- names/identities;
- numbers and dates;
- URLs;
- negation;
- modality/uncertainty;
- question vs statement;
- speaker attribution and turn structure;
- quote distinction;
- temporal marker order/chronology;
- actor identity order for known members;
- unsupported romantic/causal interpretation;
- unsupported content-token additions;
- tokens copied exclusively from a historical style example.

The regression case `بوسان -> ژاپن` is explicitly rejected even when the historical example says `ژاپن`.

Any factual failure rejects the style candidate. A style score can never override a fidelity failure.

## Allowed transformations and fallback

The foundation safely permits surface/form changes such as Persian character normalization, spacing and punctuation cleanup. The architecture permits future wording/rhythm/emoji/presentation changes, but those remain constrained by the same factual lock.

Fallback is always the faithful factual Persian draft when:

- the Translation Fusion result is not fidelity-ready;
- the content profile lacks enough real evidence;
- example retrieval fails;
- the style provider fails;
- the factual lock rejects the candidate;
- style confidence is low or the candidate is over-stylized/unnatural.

A style failure never blocks normal private-review delivery.

## AI-like writing controls

Separate style diagnostics flag high-confidence patterns such as formal boilerplate connectors, generic explanatory/emotional filler, excessive emoji density, duplicated restatement, and over-cute phrasing when the measured factual profile does not support it.

These are style diagnostics only; factual fidelity remains a separate hard gate.

## Durable metadata

No new database is introduced. Bounded metadata is stored inside the existing Event durable-state namespace:

- factual draft fingerprint;
- style candidate fingerprint;
- content type / profile;
- selected historical example IDs;
- fidelity result;
- style score;
- fallback reason;
- review-required flag;
- provider/mode.

Neither factual nor candidate bodies are duplicated in durable style state.

## Future edit feedback

`StyleEditFeedback` defines a future-safe metadata interface for:

`factual draft -> bot style draft -> user's final edit`

The record can distinguish unclassified feedback, factual correction, style preference, category-specific preference, and one-off wording. It explicitly records `auto_learn=false`. This phase does not fine-tune, globally mutate rules, or learn a global preference from one edit.

## Shadow integration and independence

The private-review hook runs Event/Timeline/Translation shadow analysis first and style evaluation afterward. Exceptions in either shadow path are observed and then the existing private-review delivery continues unchanged.

Fanfic/AO3 remains lazy and independent. The style layer is not imported by the standalone Fanfic path. Concert media identity remains independent from Event/Segment/style text identity; style never deduplicates or collapses media.

## Validation

PR CI runs the canonical checks on the exact PR HEAD:

- `python -m pip check`
- `python -m compileall -q app tests tools`
- `python -m app --check`
- `python -m unittest discover -s tests -p "test_*.py" -v`
- `python -m tools.run_channel_style_rewrite_benchmark`
- `python -m tools.run_translation_fusion_benchmark`
- the existing bounded EN/KO/JA translation production smoke

The Channel Style Rewrite benchmark contains exactly 40 deterministic cases. It does not claim human/editorial style certification; the existing manual human benchmark remains separate.
