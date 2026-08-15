# User-Voice Calibration Foundation

Status: **isolated development phase; shadow-only; not authoritative for Telegram**.

## Goal

Calibrate the already-safe Channel Style Rewrite against confirmed real user behavior without allowing historical examples or user edits to become factual authority.

The immutable authority order remains:

`source evidence -> Translation Fusion/Fidelity -> faithful factual Persian -> Channel Style Rewrite -> private review`

Calibration may influence **how style examples are ranked** only after repeated, eligible evidence. It never changes source facts, Translation Fusion, Event/Timeline membership, Phase 2/3 lifecycle, media, receipts, seen/delivered state, or public publishing.

## Production review/edit data audit

The existing private-review system already provides several useful canonical references:

- `StateStore.archive` owns canonical `Update` records and `Update.id`.
- `StateStore.drafts` owns the current `Draft`, including `Draft.update_id`, `Draft.event_key`, caption, mode and Telegram message id.
- `ArchiveStore` mirrors searchable update/caption data in `private-review.sqlite3`.
- `ReviewInboxStore` tracks `draft_id`, `update_id`, mutable status (`pending`, `ready`, `rejected`), category, source and timestamps.
- Channel Style Rewrite shadow metadata owns factual/candidate fingerprints, Event/Segment ids, content type, example ids, fidelity status and style score without persisting the candidate/factual bodies.

What the current flow **does not reliably preserve** is a distinct confirmed final user-edited body. The `copy` action sends the current draft caption unchanged, `reject` marks it rejected, and `funnier` / `softer` / `precise` replace the bot draft with another bot-generated caption. There is no canonical free-form final-edit capture or immutable action/edit history in the current private-review flow.

`ChannelStyleMemory` also contains an older `translation_feedback` table and an explicit `add_confirmed_feedback(..., confirmed=True)` API. Existing tests guarantee generated/rejected drafts are not automatically learned. No active production private-review call path was found that supplies a confirmed final user edit to that API.

Therefore, at this phase baseline:

- recoverable real `(faithful factual draft, shadow candidate, actual final user edit)` triplets from repository-owned paths: **0 confirmed**;
- real calibration set: **0**;
- real holdout set: **0**;
- user-voice quality cannot be statistically or editorially certified yet.

This phase does **not** invent fake user edits and does **not** add a second review system.

## Calibration record

`VoiceCalibrationRecord` is privacy-safe durable evidence metadata. It contains only bounded references/features:

- Update id;
- Event id / Segment id when traceable;
- content type;
- factual draft fingerprint;
- shadow candidate fingerprint;
- final user-edit fingerprint;
- one or more edit labels;
- numeric/boolean style-delta features;
- fidelity result/failures;
- confidence;
- `eligible_for_learning`;
- review action/timestamp references when available;
- `auto_learn=false`;
- `mode=shadow`;
- `text_persisted=false`.

Full bodies are analysis-time inputs only. If future offline calibration needs full text it must resolve it from canonical review/archive evidence rather than duplicating message bodies into Durable State.

## Edit classification

Conservative labels are:

- `factual_correction`
- `style_preference`
- `category_specific_preference`
- `one_off_wording`
- `formatting_preference`
- `tone_preference`
- `shortening`
- `expansion`
- `emoji_symbol_preference`
- `dialogue_format_preference`
- `rejected_bot_artifact`
- `ambiguous`
- `unclassified`

A record may have multiple labels. Any factual-lock failure is treated conservatively as factual/correction evidence and cannot tune style.

## Learning eligibility

An edit can influence future **shadow ranking** only if all of the following hold:

1. factual text -> final edit passes the existing Style Rewrite fidelity hard gate;
2. the record is traceable to a canonical Update;
3. content type is known;
4. no unresolved Translation Fusion conflict is present;
5. intent is classifiable with sufficient confidence;
6. it is not a factual correction, ambiguous record, safety/fidelity repair, or low-signal unclassified fix.

The user's final edit is stronger style evidence than historical examples only under those conditions. It is never factual evidence.

## Observable style deltas

The foundation measures behavior, not personality:

- relative length / shortening / expansion;
- line breaks;
- punctuation;
- emoji/symbol density;
- formality marker removal/addition;
- reaction-language change;
- Persian/Latin code-switching change;
- dialogue-marker change;
- bounded lexical-change ratio;
- repeated AI-like findings removed/added.

Raw replacement phrases are not persisted in calibration state.

## Global vs category preferences

No single edit changes a global rule.

- global preference: at least **3 eligible records** across at least **2 content categories**, with >= 67% direction consistency;
- category preference: at least **3 eligible records** in that category, with >= 67% direction consistency;
- repeated AI-like removal: at least **3 eligible records**;
- one-off wording remains local evidence and is not promoted.

These thresholds are intentionally conservative and deterministic.

## Ranking-only mechanism

`calibrate_example_ranking()` can re-rank existing structural style examples using only repeated preference signals. It:

- never changes example text;
- never supplies examples as factual context;
- preserves the existing `MAX_STYLE_EXAMPLES = 5` cap;
- caps each total score adjustment at `MAX_RANKING_DELTA = 0.35`;
- applies category signals only inside that category;
- never calls Translation Fusion or changes the factual draft.

Because `AUTO_LEARN=false`, production does not invoke this ranking adjustment automatically in this phase.

## Holdout and overfitting protection

Calibration records have a stable SHA-256-based 80/20 partition. Holdout ids are never fed into preference derivation in a proper evaluation run.

The deterministic benchmark also reads a bounded set of real historical corpus **ids** and verifies an isolated 80/20 split. Historical corpus text remains style material only; that split does not pretend historical messages are user-edit triplets.

Safeguards include:

- no exact-sentence copying rule;
- no topic/fact ranking signal;
- no recency boost;
- no one-off slang promotion;
- no recent-edit global override;
- no accidental typo promotion without repeated meaningful evidence;
- existing historical fact leakage and unsupported-addition gates remain authoritative.

## Reversibility and Durable State

`CalibrationSnapshot` stores:

- calibration version;
- snapshot id / previous snapshot id;
- evidence record ids;
- previous weights;
- new bounded weights;
- category;
- reason/confidence;
- repeated preference signals.

`rollback_weights()` returns the exact previous weight map. The existing Event durable-state sanitizer is extended only with a nested, bounded `voice_calibration` metadata object. No new database or schema backend is introduced. Canonical style corpus shards are never rewritten.

## Runtime safety

`event_fusion_private_runtime` only installs calibration **state compatibility** lazily in the existing private-review shadow chain. It does not feed records, derive preferences, apply ranking, or replace candidates automatically.

Existing private-review delivery remains authoritative even if shadow analysis fails.

Fanfic/AO3 remains independent: importing/running `app.fic_digest` does not require Channel Style Rewrite or User-Voice Calibration.

Concert calibration is text-only; there is no media dedupe/collapse/performance-identity code in this module.

## Free-project constraint

No paid LLM, embedding, translation API, vector database, Supabase, Redis, queue, hosting, or paid X API is introduced by this phase.

## Benchmark interpretation

`python -m tools.run_user_voice_calibration_benchmark` validates deterministic mechanics including:

- eligibility/exclusion;
- repeated global/category/AI-like signals;
- bounded ranking;
- holdout isolation;
- rollback;
- no durable full-text persistence;
- existing factual hard gates;
- historical leakage protection;
- before/after shadow fidelity.

Synthetic benchmark success is **not** evidence that the bot already sounds exactly like the user. Real user-voice quality remains dependent on naturally captured, confirmed edit evidence.
