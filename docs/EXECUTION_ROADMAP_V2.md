# Jeonghan Editorial Assistant — Execution Roadmap v2

This roadmap turns Product North Star v2 into an implementation sequence. It is intentionally incremental: preserve the working bot, prove production truth first, then replace weak layers one at a time.

## Final product target

The bot should become a source-first personal editorial twin for the admin's Telegram channel. It should:

- collect all configured Jeonghan-relevant source updates with explicit per-source completeness evidence;
- never silently discard observations;
- finish one source before moving to the next in the main review flow;
- preserve original text, provenance and media independently from translation/caption generation;
- download and prepare media without requiring a second Telegram downloader bot;
- produce accurate natural Persian translation;
- produce channel-ready captions in the admin's learned real voice, humor and fangirl intensity;
- select the right context-specific channel theme, including recurring families such as Jeonghan Instagram and BANILA CO/brand posts;
- render mixed Persian/Latin/date/symbol headers correctly for Telegram RTL/bidirectional behavior;
- learn from approved historical channel content, explicit corrections and future edit deltas;
- use admin-selected Tumblr accounts only as low-authority visual-language inspiration;
- reduce the admin's work to exception review rather than manual reconstruction of every post.

Public auto-publishing remains out of scope unless separately authorized.

## Non-negotiable engineering rules

1. Never work directly on `main`; use branch -> tests -> commit -> PR.
2. Never force-push main, mutate credentials, use sudo without reason, delete unrelated/personal files, or touch unrelated repositories.
3. The Persian Literary Translation Engine repository is strictly out of scope.
4. No big-bang rewrite.
5. Python remains the volatile integration layer; Rust is introduced only for deterministic truth/state.
6. Initial Rust/Python boundary is versioned JSON/JSONL or subprocess IPC; no PyO3 unless later profiling proves a need.
7. SQLite remains the default persistence layer for the single-admin bot.
8. Existing behavior stays available behind compatibility paths/feature flags until replacement paths prove safe.
9. AI may classify, translate, rank and draft; AI may never decide that an observed source post never existed.
10. Translation fidelity outranks personal style.
11. Explicit admin corrections outrank all inferred style.
12. Every phase needs tests and a rollback path.

## Evidence hierarchy for personalization

Highest to lowest authority:

1. explicit admin instructions/corrections;
2. final posts actually approved/posted by the admin;
3. recent context-specific historical channel examples;
4. broader historical channel corpus;
5. repeated inferred behavioral patterns;
6. admin-selected Tumblr visual inspiration;
7. generic model creativity.

One unusual edit must not become a permanent preference. Durable inferred preferences require repeated evidence unless explicitly stated by the admin.

---

# Stage A — Establish truth before migration

## Phase 0 — Production Truth Audit

### Goal

Create an evidence-backed map of what the bot truly does today versus what green CI/runtime status implies.

### Work

- inventory all configured X/source accounts and their intended order;
- trace source -> collector -> filtering -> dedupe -> organizer -> media -> translation -> Telegram delivery;
- verify which collectors are authoritative per source;
- record where retweets, replies, quotes, media-only posts, generic posts and thread continuations are dropped;
- verify all cursor advancement rules;
- compare production workflow success with real source completeness;
- identify all places where failures are converted into fallback success;
- identify existing useful tests/contracts that must be preserved.

### Deliverables

- source coverage matrix;
- silent-drop matrix;
- cursor/completeness matrix;
- production failure/fallback matrix;
- concrete migration dependency map.

### Exit gate

No architecture work begins until the team can answer for every configured source: what was requested, what was actually retrieved, what was dropped, why, and whether the window was proven complete.

---

# Stage B — Make collection auditable and source-first

## Phase 1 — Raw Observation Store

### Goal

Persist first; classify later.

### Work

Introduce a versioned raw observation contract containing at minimum:

- provider/source;
- configured source ID/handle;
- external post ID;
- timestamps;
- original text and structured quoted/reply context where available;
- media references;
- post type metadata;
- retrieval attempt/provenance;
- raw provider payload reference/hash where practical;
- observation status/errors.

Every observation must be stored before relevance logic runs.

### Tests

Regression fixtures for:

- media-only posts;
- generic captions;
- replies;
- quotes;
- unfamiliar nicknames;
- duplicate observations;
- provider retry.

### Exit gate

A post can be classified hidden later, but cannot disappear before persistence.

## Phase 2 — Source Ledger

### Goal

Make each account an independently auditable unit.

### Work

Create source/window state for:

- source ordering;
- requested time window;
- cursor before/after;
- attempts and retry state;
- oldest/newest observed timestamps;
- raw count;
- classification counts;
- media counts/failures;
- completion evidence;
- COMPLETE / PARTIAL / UNPROVEN state.

Retry must be source-specific.

### Exit gate

The system can answer `what happened for @source during this window?` without relying on global workflow status.

## Phase 3 — Rust Editorial Core Foundation

### Goal

Move deterministic truth/state into a small Rust core without rewriting collectors.

### Rust owns

- source ledger contracts;
- review-window state transitions;
- cursor advancement invariants;
- deterministic source ordering;
- dedup/idempotency keys;
- explicit completeness state transitions;
- editorial queue state primitives.

### Python keeps

- X/twscrape;
- external scraping;
- media adapters;
- AI providers;
- Tumblr collection;
- Telegram integration initially.

### Boundary

Use a versioned JSON contract and subprocess/IPC layer first.

### Exit gate

Rust tests prove invalid cursor/completeness transitions cannot occur, while the Python production runtime remains usable.

## Phase 4 — Completeness Engine

### Goal

Make `complete` mean evidence-backed completeness, not `the workflow did not crash`.

### Work

Define proof rules per source/provider. A source may advance its durable cursor only when its requested window is COMPLETE.

PARTIAL/UNPROVEN windows must:

- remain retryable;
- expose failure reason;
- preserve observations already found;
- never masquerade as complete.

### Exit gate

The admin can trust the source badge enough that opening X purely to confirm `did the bot miss something?` becomes unnecessary for proven-complete windows.

## Phase 5 — Source-first Editorial Queue

### Goal

Replace event-first presentation as the main workflow.

### UX invariant

Finish source A before source B unless the admin explicitly defers A.

### Work

- stable configured source order;
- source progress (`3/31` etc.);
- post progress within each source;
- source status summary;
- resume/defer/retry source state;
- keep old event view as an optional compatibility view during rollout.

### Exit gate

Normal review no longer interleaves accounts chronologically.

## Phase 6 — No-Silent-Drop Relevance

### Goal

Relevance becomes a reversible classification, never destructive filtering.

### States

- RELEVANT
- UNCERTAIN
- NOT_RELEVANT

### Work

- classify only after raw persistence;
- show relevant + uncertain normally;
- collapse NOT_RELEVANT by default but count it;
- add `Show hidden`;
- retain classification reason/confidence;
- allow future reclassification.

### Exit gate

No keyword, weak model score or media-only structure can silently erase an observed post.

---

# Stage C — Replace the manual media/translation workflow

## Phase 7 — Media Asset Pipeline

### Goal

Remove the separate Telegram downloader step.

### Work

For every media asset track:

- stable asset identity;
- parent source/post provenance;
- original URL/reference;
- media type;
- download state;
- local/artifact path or durable reference;
- checksum/size/metadata where practical;
- retry count;
- explicit failure reason.

Keep yt-dlp/gallery-dl/FFmpeg and similar mature tooling in Python.

### Exit gate

For supported media, the editorial inbox has usable media without the admin opening a second downloader bot.

## Phase 8 — Translation & Caption Artifact Pipeline

### Goal

Make original, translation and caption independent recoverable artifacts.

### Work

Track states such as:

- ORIGINAL_READY
- TRANSLATION_PENDING
- TRANSLATION_READY
- TRANSLATION_FALLBACK
- CAPTION_PENDING
- CAPTION_READY
- NEEDS_REVIEW

`Retranslate` must not recollect the source. `Rewrite` must not retranscribe or redownload media.

### Translation rules

- preserve factual meaning;
- natural colloquial Persian;
- maintain speaker/context distinctions;
- preserve useful fandom names/terms consistently;
- never hide provider fallback behind a confident-looking final caption.

### Exit gate

A provider outage can degrade one artifact without losing source/media or forcing recollection.

---

# Stage D — Build the admin's real voice memory

## Phase 9 — Historical Channel Corpus Ingestion

### Goal

Use the Telegram history as the primary personalization dataset instead of asking the admin to hand-write style rules.

### Known corpus behavior

The historical exports span from the channel's start in May 2023 through later history. Multiple exports may overlap or be truncated; ingestion must merge safely instead of treating one export as authoritative.

### Work

- normalize Telegram export message structures;
- merge by channel ID + message ID and stable metadata;
- tolerate truncated exports and retain all valid parsed content;
- preserve message date and edited date separately;
- extract textual content from rich Telegram `text` arrays without losing entities/links/style metadata;
- distinguish service/media-only/sticker/message records;
- preserve media metadata even when actual media files were not included;
- create corpus manifest with date coverage, counts and provenance;
- weight more recent style examples more strongly while retaining older evolution history.

### Privacy/cost rule

Do not send the entire raw archive to an LLM every time. Transform it into structured memory + retrievable real examples.

### Exit gate

A reproducible corpus build can be regenerated from exports without duplicate training examples or silent data loss.

## Phase 10 — Voice DNA Extraction

### Goal

Model how the admin actually writes, not generic `cute K-pop Persian`.

### Extract

- colloquial Persian grammar and recurring phrasing;
- nicknames/fandom vocabulary;
- code-switching with English/Korean/Japanese;
- humor/teasing patterns;
- affectionate/fangirl reactions;
- emotional intensity distributions;
- emoji/emoticon habits;
- punctuation/repetition/stretched forms;
- short reaction vs explanatory caption behavior;
- factual vs emotional writing modes;
- category/context-specific voice differences;
- negative patterns inferred from future corrections.

### Representation

Store structured features/preferences plus curated real examples with provenance, recency and context. Do not reduce the identity to one giant prompt.

### Evaluation

Hold out historical posts and compare personalized generation with the current generic style path.

### Exit gate

Personalized outputs require materially fewer edits on held-out examples.

## Phase 11 — Online Feedback Memory

### Goal

Make every meaningful admin action improve future outputs.

### Feedback semantics

- Ready without edit -> positive evidence;
- manual edit -> highest-value generated->final correction pair;
- Retranslate -> translation negative evidence, not automatic style rejection;
- Rewrite -> caption/style negative evidence;
- explicit notes like `less symbols`, `more fangirl`, `too formal`, `softer` -> direct preference rule;
- repeated deletion/addition -> emerging negative/positive pattern;
- Skip -> not automatically style-negative unless a reason is known.

### Work

- global vs context-specific preferences;
- preference confidence;
- recency weighting;
- explicit-rule locking/authority;
- inspect/reset/correct learned preferences;
- rejected-example memory.

### Exit gate

The same corrected mistake stops recurring after sufficient evidence.

---

# Stage E — Learn the channel's visual language and themes

## Phase 12 — Theme Family Discovery from Channel History

### Goal

Learn that the channel has multiple recurring visual templates, not one universal header.

### Explicit known families

- Jeonghan Instagram posts;
- BANILA CO / brand-related posts.

### Discover automatically

Potential families such as:

- X updates;
- official programs/shows;
- magazines/editorials;
- airport/travel;
- fansign/fan content;
- member Instagram;
- photo dumps;
- interviews;
- shipping/fandom;
- funny reactions;
- sentimental posts;
- other recurring clusters evidenced by the corpus.

### Default/general grammar

For ordinary updates, learn variants of:

`date -> symbol/emoji or symbol+emoji -> program/event/story/source label -> body`

This is a grammar, not a single rigid template.

### Exit gate

The engine selects an appropriate family/context instead of applying random decoration.

## Phase 13 — Tumblr Visual Inspiration Layer

### Goal

Use the ten admin-selected Tumblr sources to expand the visual vocabulary while preserving the channel's identity.

### Learn only reusable visual grammar

- symbols;
- separator combinations;
- micro-layout;
- whitespace;
- date/source formatting;
- compact vs decorative structures;
- mood/theme combinations.

### Never

- treat Tumblr as factual Jeonghan source;
- include it in source completeness;
- copy distinctive creator captions verbatim;
- let a Tumblr trend override stable admin preferences.

### Exit gate

Tumblr contributes fresh aesthetic possibilities, but generated posts remain recognizably the admin's channel.

## Phase 14 — RTL/Bidirectional Theme Renderer

### Goal

Make Persian channel headers visually correct in Telegram.

### Requirement

RTL is a functional correctness issue, not polishing.

### Work

- represent header structure semantically rather than naive string concatenation;
- handle Persian + Latin + date digits + emoji + Unicode ornaments;
- preserve known-good historical layouts;
- use directionality controls only deliberately and test their Telegram rendering impact;
- store logical structure separately from final rendered string;
- build regression fixtures for date + ornament + Persian label and date + ornament + Latin label;
- ensure symbols do not visually jump, reverse or attach to the wrong segment.

### Exit gate

Representative headers render predictably in Telegram without manual rearrangement.

## Phase 15 — Context-aware Theme Engine

### Goal

Combine voice + content context + theme family + RTL-safe renderer into a coherent design system.

### Inputs

- post/source type;
- content mood;
- historical family matches;
- recent approved examples;
- explicit rules;
- Voice DNA;
- Tumblr-inspired visual candidates;
- RTL constraints.

### Output

A channel-ready caption with deliberate visual styling, not decoration added after generation.

### Exit gate

On historical held-out cases, the selected theme is usually the same family the admin would choose and requires materially less restyling.

---

# Stage F — Make Telegram the whole editorial workstation

## Phase 16 — Editorial Inbox v2

### Goal

Put the complete source-first workflow in one private Telegram assistant.

### Per-source UI

- source name/order;
- COMPLETE/PARTIAL/UNPROVEN;
- raw/relevant/uncertain/hidden counts;
- media failures;
- retry/defer/status actions.

### Per-post UI

- media;
- original;
- Persian translation;
- channel-ready personalized + themed caption;
- source/context details;
- classification state.

### Actions

- Ready
- Edit
- Retranslate
- Rewrite
- Original
- Show context
- Skip
- Next
- Show hidden
- Retry source
- Defer source

Edits feed Voice/Theme memory.

### Exit gate

The admin can complete routine editorial work without opening X, a downloader bot or a separate AI chat.

## Phase 17 — Fast Interactive Control Plane

### Goal

Make Telegram interaction responsive and independent from slow scheduled collection runs.

### Work

- separate interactive commands/state from best-effort scheduled collectors where useful;
- keep GitHub Actions for CI, validation, backups/recovery and suitable schedules;
- do not use workflow-green status as completeness proof;
- preserve resumable review sessions.

### Exit gate

Telegram actions feel interactive even when collectors/retries are running separately.

---

# Stage G — Controlled autonomy

## Phase 18 — Assisted Autonomy

### Goal

Reduce repetitive approvals without removing admin authority.

### Behavior

- high-confidence routine items arrive fully prepared;
- unusual/low-confidence cases show alternatives or require explicit review;
- remember safe repeated choices;
- suggest coherent theme treatment for event/cluster batches;
- never public-post automatically without separate explicit authorization.

### Exit gate

Most ordinary items need approval rather than rewriting/restyling.

## Phase 19 — Optional Telegram Rust Migration

Only evaluate teloxide/Rust for Telegram control-plane components after the Rust editorial core is stable and current Telegram functionality has parity tests. This is optional optimization, not a required milestone for product success.

---

# Stage H — Continuous verification

## Phase 20 — Coverage & Personalization Verification

Track whether the product actually replaced manual work.

### Collection KPIs

- configured-source coverage rate;
- complete/partial/unproven rate;
- silent miss rate;
- median detection lag;
- duplicate observation/delivery rate;
- source retry success.

### Media KPIs

- media extraction/download success;
- retry success;
- unsupported media rate.

### Editorial KPIs

- translation acceptance/retranslation rate;
- first-pass caption acceptance;
- average manual caption edits;
- first-pass theme acceptance;
- average manual restyling;
- repeated-error rate after correction;
- percentage of items restyled from scratch.

### Product North Star KPI

How often does the admin still need to open X or another tool because they do not trust the assistant or because the assistant cannot finish the workflow?

The desired long-term answer is: only for exceptional cases, not routine channel operation.

---

# Recommended implementation order

Do not start with the visually exciting personalization work before source truth is reliable.

Critical path:

`0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14 -> 15 -> 16 -> 17 -> 18 -> 20`

Phase 19 is optional and can be deferred indefinitely.

Some safe preparation can overlap:

- corpus analysis for Phases 9-12 may be developed offline while Phases 1-6 are being implemented;
- Tumblr feature extraction can be prototyped offline, but must not reach production styling until the channel corpus/authority hierarchy is implemented;
- RTL fixtures can be built early from historical examples.

## Rollout strategy

For all behavior-changing phases:

1. implement behind a feature flag/shadow mode;
2. run old and new path on the same windows where practical;
3. compare outputs/completeness;
4. preserve production fallback;
5. switch the admin-facing default only after acceptance gates pass;
6. remove legacy paths only after sustained production confidence.

## Definition of done

The project is not done when CI is green or a collector runs successfully.

It is done when, for routine channel work, the admin can open the private Telegram assistant, see source-by-source proof of what was collected, receive media + accurate Persian + a caption that genuinely sounds and looks like their channel, approve/edit exceptions, and finish without manually reproducing the old X -> downloader -> AI -> styling workflow.