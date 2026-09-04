# Jeonghan Editorial Assistant — Product North Star v2

## Why this exists

The previous product direction optimized the bot as a chronological/event-oriented collector and private review assistant. That architecture is useful, but it does not fully replace the admin's real manual workflow.

The new product goal is not merely to collect Jeonghan updates. The bot must become a trustworthy editorial inbox that removes the need to manually open X just to verify completeness, download media separately, ask another AI for translation/caption help, and manually style every channel post.

## North Star

The admin should be able to open one private Telegram assistant and complete the full Jeonghan channel workflow there:

1. retrieve all configured sources for a requested review window;
2. prove source-by-source completeness rather than relying on a green workflow status;
3. review one source completely before presentation moves to the next source;
4. preserve every raw observation before relevance classification;
5. never silently discard a post because of keyword filtering, missing text, media-only content, a weak AI classification, or a transient translation failure;
6. download and prepare usable media;
7. preserve the original source text;
8. produce accurate, natural Persian translation;
9. produce a channel-ready caption and visual/title styling that matches the admin's taste;
10. let the admin approve, edit, regenerate, reveal hidden items, skip, retry, and continue without leaving Telegram;
11. learn the admin's own writing voice, humor, fangirl reactions, recurring expressions, emotional intensity, formatting habits and visual taste from explicit feedback and approved channel history;
12. progressively handle routine editorial and styling decisions without requiring the admin to manually rewrite or restyle every post from scratch.

Success means the admin no longer needs to open X merely to check whether the assistant missed something, and no longer needs to manually reconstruct their own voice and channel aesthetic on every post.

## Product identity: a personal editorial twin, not a generic caption bot

The assistant should behave like a learned editorial extension of the admin, not like a generic social-media writer.

It must gradually understand distinctions such as:

- how the admin sounds when excited, emotional, teasing, affectionate, dramatic, sarcastic, amused, nostalgic or simply informative;
- what kinds of fangirl reactions feel natural versus forced;
- which jokes, exaggerations, punctuation patterns, emoji habits and sentence rhythms feel like the admin;
- when the admin prefers a clean factual translation and when a post should feel more playful or emotionally expressive;
- how much decoration is appropriate for a particular post type;
- which titles, symbol combinations, whitespace patterns and decorative structures look beautiful to the admin and which feel cluttered, generic or artificial;
- differences between the admin's voice and the original source's voice, so translation fidelity is never sacrificed merely to sound personal.

The system must learn these preferences from behavior over time rather than repeatedly asking the admin to restate them.

### Voice learning hierarchy

Use evidence in this order:

1. explicit admin corrections, instructions and rejected/approved alternatives — highest authority;
2. final versions actually approved or posted by the admin;
3. historical channel corpus and recurring language patterns;
4. context-specific patterns inferred from repeated behavior;
5. Tumblr visual-language inspiration for styling only;
6. model priors and generic creativity — lowest authority.

The assistant must never fabricate a permanent preference from one isolated choice. Stable preferences should require repeated evidence or explicit instruction.

### Personal style memory should capture

- common Persian phrasing and colloquial structures;
- English/Korean/Japanese terms the admin keeps untranslated or translates consistently;
- preferred nicknames and fandom terminology;
- recurring fangirl expressions and reaction styles;
- preferred humor and teasing patterns;
- emoji and emoticon habits;
- punctuation, ellipsis, repetition and capitalization tendencies;
- phrases the admin dislikes or considers too formal, too AI-like, too cringe or too generic;
- preferred intensity by context;
- title/header habits;
- symbol families and separator combinations;
- compact versus decorative layouts;
- styling differences for X updates, interviews, magazine shoots, airport photos, official posts, fan content, memes, sentimental posts, shipping content and other recurring categories.

### Feedback loop

Every meaningful admin action should become structured style evidence rather than disappearing after one interaction.

Examples:

- `Ready` with no edits -> positive evidence for the generated form;
- manual text edit -> high-value correction pair: generated -> accepted final;
- `Retranslate` -> translation-quality negative signal, not necessarily style rejection;
- `Rewrite` -> caption/style negative signal;
- `too formal`, `too much`, `less symbols`, `more fangirl`, `softer`, `funnier`, etc. -> explicit tagged preference evidence;
- repeated deletion of a symbol or phrase -> emerging negative preference;
- repeated addition of the same structure -> emerging positive preference.

The memory layer must distinguish global preferences from context-specific preferences. For example, liking decorative symbols for magazine posts does not imply using them on every factual update.

### Safety against style drift

- Do not overwrite established style from a single Tumblr trend or one unusual post.
- Keep explicit admin rules versioned and inspectable.
- Preserve examples of rejected outputs so the system can avoid repeating them.
- Separate factual translation fidelity from personal caption voice.
- Never imitate a specific external creator's distinctive writing verbatim.
- Prefer learning reusable patterns and combinations rather than cloning another channel.

## Product invariants

- Source-first, not event-first, for the main editorial review flow.
- Finish presentation of source A before source B begins.
- Collect first, persist first, classify later.
- No silent drops.
- AI may rank or recommend relevance; AI must not be the authority on whether an observed post existed.
- Every configured source must expose an explicit completeness state for the requested review window.
- An incomplete or unproven source must not advance its durable source cursor as if the window were complete.
- Green CI/runtime health is not equivalent to complete collection.
- Original content and provenance must remain recoverable even when translation, caption generation, media extraction, or Telegram delivery fails.
- Personal style learning must be explainable through approved examples/corrections, not opaque one-off guesses.
- Translation fidelity and source meaning always outrank stylistic imitation.
- Public auto-publishing remains out of scope unless the admin explicitly changes that policy later.
- Changes must preserve repository isolation, avoid credential mutation, avoid force-push to main, and use branch/test/PR workflows.

## Target user workflow

Old manual workflow:

X -> inspect sources -> download media through another Telegram bot -> send text to AI / write caption -> add symbols/title styling -> post to channel

Target workflow:

Jeonghan Editorial Assistant -> source-by-source review -> media + original + Persian translation + learned personal caption + learned visual styling -> approve/edit/skip -> next item

Long-term target:

Jeonghan Editorial Assistant -> understands the update -> prepares everything in the admin's established voice and channel aesthetic -> admin usually only reviews exceptions.

## Target review UX

A review window should present explicit source progress and evidence, for example:

```text
12:00–14:00 REVIEW WINDOW

@jeonghannisms
Source 3/31
Status: COMPLETE
Raw: 12
Relevant: 9
Uncertain: 1
Hidden: 2
Missing media: 0

Post 1/10
[media]

Original:
...

Persian:
...

Channel caption:
...

[Ready] [Edit]
[Retranslate] [Rewrite]
[Original] [Show context]
[Skip] [Next]
```

The next source should not be presented until the current source's review batch is finished or explicitly deferred.

## Hybrid architecture decision

Do not rewrite the whole application in Rust.

### Python remains the integration/adaptation layer

Keep Python where its ecosystem and current project integrations are strongest:

- X retrieval and `twscrape` adaptation;
- media extraction/downloading through direct URLs, yt-dlp, gallery-dl, FFmpeg and related adapters;
- AO3/HTML scraping;
- Gemini/LLM adapters;
- translation and caption generation experiments;
- external provider-specific code that may need rapid patches when a website changes.

### Rust becomes the deterministic editorial core

Introduce Rust incrementally for stateful behavior where silent ambiguity is unacceptable:

- source ledger;
- per-source cursors;
- review-window contracts;
- completeness proof/state;
- deterministic source-first ordering;
- deduplication/idempotency contracts;
- editorial queue state machine;
- delivery receipts;
- explicit invariants and state transitions;
- durable structured style-feedback events and versioned preference state where deterministic behavior matters.

Initial Rust/Python integration should prefer a versioned JSON contract or similarly explicit process boundary. Do not introduce PyO3/native extension coupling until there is evidence that IPC is a real bottleneck.

SQLite remains appropriate for the current private single-admin product unless scaling requirements materially change.

## Relevance model

Keyword filtering must stop being a destructive pre-persistence gate.

Every observed post should first be stored, then classified as one of:

- RELEVANT
- UNCERTAIN
- NOT_RELEVANT

Relevant and uncertain items are visible in the normal editorial flow. Not-relevant items may be collapsed by default but must remain countable, auditable, and revealable with a `Show hidden` action.

Media-only posts, generic captions, thread continuations, unfamiliar nicknames, quotes, replies, or weak textual signals must not vanish merely because a keyword was absent.

## Completeness model

For every source and requested window, retain evidence such as:

- source handle;
- expected window start/end;
- retrieval attempt ID;
- cursor before/after;
- newest and oldest observed timestamps;
- pagination exhaustion/completion state;
- raw post count;
- reply/quote/retweet counts when supported;
- relevant/uncertain/hidden counts;
- media status;
- retrieval/provider errors;
- explicit COMPLETE / PARTIAL / UNPROVEN state.

If completeness cannot be proven, report it to the admin and keep that source retryable without pretending the whole review window is complete.

## Translation and caption behavior

Original text is authoritative and preserved.

Translation and caption are separate derived artifacts. A provider outage must not silently transform a fallback into something that looks like a final high-confidence caption.

Useful derived states include:

- TRANSLATION_PENDING
- TRANSLATION_READY
- TRANSLATION_FALLBACK
- CAPTION_READY
- NEEDS_REVIEW

The admin should be able to regenerate translation or caption without recollecting the source post.

The caption generator must have two separate responsibilities:

1. preserve the factual meaning and context supplied by the translation/source pipeline;
2. express the final channel caption in the admin's learned voice and visual language when appropriate.

## Tumblr inspiration and visual-language learning

The assistant should gain a separate, explicitly non-authoritative inspiration layer for Tumblr accounts selected by the admin.

Purpose:

- learn the admin's preferred use of symbols, separators, Unicode ornaments, spacing, title construction, micro-layout, date/header formats, and channel decoration;
- surface reusable styling ideas;
- help the caption writer understand what "pretty", "Tumblr-like", "coquette", minimal, editorial, soft, dark, playful, or similar requested channel treatments mean for this specific admin.

This Tumblr layer is for style inspiration, not factual Jeonghan news collection.

### Required safety and quality rules

- Keep Tumblr inspiration sources logically separate from X news/update sources.
- Never mix Tumblr posts into Jeonghan update completeness counts.
- Never copy long Tumblr captions or distinctive creative text verbatim merely to imitate an account.
- Learn patterns and reusable visual grammar rather than cloning one creator.
- Preserve provenance internally so the system knows which account inspired a style pattern.
- Let the admin add/remove/weight inspiration accounts.
- Maintain an admin-owned style profile distilled from approved examples and corrections.
- Treat explicit admin corrections as stronger evidence than passive Tumblr observations.
- Do not let changing Tumblr trends silently overwrite stable channel style preferences.

### Desired style-memory model

The system should be able to learn structured signals such as:

- preferred separators and symbol families;
- acceptable symbol density;
- whitespace/indentation style;
- date placement and formatting;
- source-label formatting;
- lower-case vs upper-case tendencies;
- punctuation density;
- compact vs decorative title patterns;
- recurring framing patterns such as `260904  ꔫ`, `source  ﹒ x update`, etc.;
- styles the admin rejected;
- combinations that look cluttered or too generic;
- per-context style choices (X update, magazine, airport, photo dump, interview, fan content, etc.).

The final style generator should combine:

1. explicit admin rules and corrections — highest priority;
2. approved historical channel examples;
3. distilled Tumblr inspiration patterns;
4. model creativity — lowest priority.

## Roadmap

### Phase 0 — Production Truth Audit

Establish the real current behavior before changing architecture. Verify configured source inventory, source modes, source order, retrieval coverage, filtering, dedupe, media, translation, delivery, cursors, and production completeness reporting.

Deliverable: evidence-backed gap matrix comparing source truth -> raw collection -> classification -> delivery.

### Phase 1 — Raw Observation Store

Persist every source observation before relevance filtering. Introduce versioned raw observation contracts and provenance.

### Phase 2 — Source Ledger

Introduce per-source review-window records, counters, source-specific cursors, retry state, and explicit source ordering.

### Phase 3 — Rust Editorial Core Foundation

Create the first Rust crate around deterministic contracts and tests: source ledger, window state, completeness states, cursor advancement rules, ordering and invariants. Keep the existing Python runtime operational during migration.

### Phase 4 — Completeness Engine

Make COMPLETE/PARTIAL/UNPROVEN evidence-driven. Fail closed when the requested source window cannot be proven complete.

### Phase 5 — Source-first Editorial Queue

Implement a new source-by-source review mode. Keep legacy event grouping available only as an alternate view until the new flow proves production-safe.

### Phase 6 — No-Silent-Drop Relevance

Replace destructive keyword filtering with persisted classification and revealable hidden/uncertain ledgers. Add regression coverage for media-only posts, generic text, replies, quotes and unknown nicknames.

### Phase 7 — Media Asset Pipeline Hardening

Give each media asset stable identity, provenance, checksum/metadata where practical, download state, retry semantics and explicit failure reason while retaining mature Python media tools.

### Phase 8 — Translation/Caption Artifact Pipeline

Separate original, translation and caption states. Make provider outages explicit and recoverable. Preserve channel corpus/glossary/style work that already exists.

### Phase 9 — Personal Voice & Editorial Memory

Build a dedicated learning layer for the admin's writing identity rather than relying on one static prompt or undifferentiated corpus.

Requirements:

- record structured approve/edit/reject feedback;
- store generated-to-final correction pairs;
- distinguish translation feedback from caption/style feedback;
- infer stable preferences only from repeated evidence or explicit rules;
- maintain global and context-specific preferences separately;
- retrieve the most relevant approved examples for each new post;
- track negative examples so rejected wording and formatting are less likely to recur;
- provide a way to inspect, correct and reset learned preferences;
- never allow learned style to alter factual source meaning.

Acceptance gate: on a held-out set of real historical posts, the personalized generator should require materially fewer admin edits than the current generic/channel-style path.

### Phase 10 — Tumblr Inspiration Layer

Add admin-configured Tumblr inspiration sources and a collector that stores style examples separately from news sources. Distill reusable visual/style features into an admin-owned style memory. Feed Tumblr features into the personalized style generator below direct admin evidence.

### Phase 11 — Context-aware Theme Engine

Turn styling into a deliberate channel design system rather than random symbol insertion.

The engine should choose among learned layouts/themes based on content type and mood, while preserving overall channel coherence. Examples include official update, magazine/editorial, soft/romantic, funny/fangirl, sentimental, dark, minimalist, shipping/fandom and photo-focused treatments.

It should be able to evolve the channel aesthetic gradually while preventing abrupt style drift.

### Phase 12 — Editorial Inbox v2

Expose source progress, post progress, completeness badges, original, translation, personalized styled caption, media and actions such as Ready/Edit/Retranslate/Rewrite/Original/Skip/Show hidden/Retry source.

Feedback actions should feed the style/voice memory automatically.

### Phase 13 — Assisted Autonomy

After enough validated style evidence exists, reduce repetitive manual work without removing control.

Possible behavior:

- high-confidence routine posts arrive already formatted in the most likely accepted style;
- low-confidence or unusual posts receive alternatives or request review;
- repetitive safe choices can be remembered automatically;
- the assistant can suggest a channel theme treatment for a cluster/event;
- the admin remains the authority and can override any learned behavior.

Do not enable public auto-publishing as part of this phase unless separately and explicitly authorized.

### Phase 14 — Faster Telegram Control Plane

Separate interactive Telegram control from best-effort scheduled collection where appropriate. Preserve GitHub Actions for validation, backup/recovery and suitable scheduled jobs; do not treat scheduled workflow success as the sole proof of source completeness.

### Phase 15 — Optional Telegram Rust Migration

Only after the Rust editorial core is stable and feature parity is proven, evaluate moving Telegram control-plane components to Rust/teloxide. No big-bang rewrite.

### Phase 16 — Continuous Coverage & Personalization Verification

Track product KPIs that measure whether the assistant actually replaces manual work:

- source coverage rate;
- silent miss rate;
- unproven-source rate;
- median detection lag;
- media success rate;
- translation/caption readiness;
- duplicate-delivery rate;
- caption first-pass acceptance rate;
- average manual edits per accepted caption;
- style-regeneration rate;
- repeated-error rate after explicit correction;
- number of times the admin still has to open X to verify completeness;
- number of routine posts the admin still has to manually restyle from scratch.

The last two metrics are core North Star metrics: needing X for verification or repeatedly rebuilding style manually means the product still has work to do.

## Implementation discipline

- inspect before editing;
- add tests before or alongside behavior changes;
- use small migration phases, never a big-bang Rust rewrite;
- preserve the current working production path until replacement behavior has parity and evidence;
- branch -> tests -> commit -> PR -> review/merge;
- do not mutate credentials;
- do not force-push main;
- do not modify unrelated repositories;
- do not delete personal/project data as part of refactoring;
- keep public auto-posting disabled unless explicitly authorized later;
- update this North Star when an accepted product decision changes it.

## Definition of done

The product is not done because CI is green or because all services are technically running.

It is done when the admin can trust one private assistant to tell them what happened, prove what was checked, prepare the media and text in their own learned voice and channel aesthetic, and complete the review workflow without manually reproducing the old X -> downloader -> AI -> styling pipeline.

Long-term success means the assistant is recognizably aligned with the admin's editorial identity: it understands how they usually speak, joke, fangirl, react, title, decorate and structure posts well enough that routine content normally needs review rather than reconstruction.
