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

## Cross-cutting audited hardening track — 2026-09-18

These tasks came from a repository-by-repository GitHub capability audit. They must remain isolated from behavior-changing migrations and be adopted only where they close a demonstrated gap.

1. **Workflow security now:** add CI-only, pinned `actionlint` for blocking GitHub Actions correctness checks and pinned `zizmor` initially report-only for workflow security findings. Neither belongs in production dependencies.
2. **SQLite backup correctness:** harden private-state backup by using Python's SQLite online backup API to create a consistent temporary snapshot before validation/encryption; do not depend solely on prior workflow WAL checkpoint ordering.
3. **X parser regression fixtures:** capture representative provider payloads and lock parsers/backends with offline fixture tests. Reuse Hani's existing collector chain rather than installing a second X stack.
4. **Translation MQM-lite:** extend the existing benchmark with deterministic error classes for omission/addition, entity/speaker, numbers/dates, tone/register, emoji/laughter and fandom/relationship nuance.
5. **Perceptual media dedupe experiment:** add only as feature-flagged/advisory grouping after exact URL/bytes/Telegram identities; never suppress near-duplicates automatically until calibrated on real channel assets.
6. **Durable-job patterns only when needed:** visibility timeout/dead-letter/once-ledger concepts may be adapted for future background jobs; do not introduce a second queue or agent framework now.

Detailed repository decisions live in `docs/research/github-capability-scan-2026-09-18.md`.

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

## Cross-cutting developer tooling & external-project adoption track — 2026-09-25

This track records external GitHub projects that may improve future deep next-stage work. These are **not blanket installation instructions**. Every item is classified as install/integrate, evaluate/prototype, inspiration-only, deferred, or no-action. Production runtime safety, repository isolation, secrets, durable state, licensing, and existing agent instructions remain authoritative.

### Priority A — evaluate for developer-side adoption

1. **codebase-memory-mcp — evaluate, then adopt developer-side if gates pass**
   - Purpose: give coding agents repository-aware structural context, impact analysis, dependency/call-graph knowledge, and faster navigation during large next-stage tasks.
   - Scope: developer tooling only; never a production bot dependency.
   - Required isolation: build/index this repository separately from every other project. Never merge or inherit memory from the Persian Literary Translation Engine.
   - Safety gates before adoption:
     - review the exact pinned upstream revision, license, install behavior, background processes, telemetry, and config writes;
     - prefer manual MCP configuration over an installer that automatically rewrites agent instructions;
     - do not allow automatic edits to `AGENTS.md`, secrets, workflows, private SQLite/state, or production configuration;
     - keep generated indexes/caches ignored and free of private review content/secrets;
     - prove uninstall/rollback and verify ordinary tests/CI do not depend on the tool.
   - Acceptance evidence: run a representative deep-maintenance task with and without it and record whether navigation/tool-call count, correctness, regression discovery, or context efficiency materially improves.

2. **Graft — evaluate after the codebase-memory-mcp benchmark**
   - Purpose: repository-context retrieval for coding agents.
   - Do not make Graft and codebase-memory-mcp mandatory simultaneously by default; first measure whether the second context system adds material value.
   - Block automatic rewriting of `AGENTS.md` or other authoritative project instructions unless the exact diff is reviewed.
   - Keep it developer-side and optional; production, CI, collection, Telegram delivery, and durable state must continue without it.
   - Adopt only if benchmark evidence shows complementary value rather than duplicated context/noise.

3. **PI-Desktop — optional external workspace, not a repository/runtime dependency**
   - Evaluate as a local-first shell for opening this project, coding agents, MCP servers, models, and workflows in one persistent developer workspace.
   - Preserve this repository as an independent workspace with its own memory/configuration.
   - Do not copy PI-Desktop architecture wholesale into the bot. Borrow workflow/UX ideas only when they solve a demonstrated developer-productivity problem.
   - Failure or absence of PI-Desktop must never affect GitHub Actions or production Telegram behavior.

### Priority B — selective research/inspiration, not bulk installation

4. **Agency-agents — selective role inspiration only**
   - Do not bulk-install the full agent catalog.
   - During a future stage, review only narrowly relevant roles such as security reviewer, test/reliability reviewer, research specialist, UX/editorial specialist, or media specialist.
   - Any adopted role must be reduced to project-specific guidance, checked for instruction conflicts, provenance/license reviewed, and kept subordinate to this repository's `AGENTS.md` and next-stage protocol.

5. **Hyperresearch — research-sidecar evaluation**
   - Consider for deep source/repository/standards research when a next-stage task requires broad evidence gathering.
   - Keep research artifacts separate from runtime state and require source/provenance review before architectural decisions are accepted.
   - Do not make the production bot dependent on a research agent framework.

6. **Needle — deferred local structured-extraction/tool-calling experiment**
   - Evaluate only if a concrete local/offline extraction or tool-routing use case appears.
   - Benchmark against the existing Python/Rust path before adding another model/runtime.
   - Disable telemetry where supported and document model/resource/licensing/privacy behavior.
   - Never use it as justification for replacing proven collection, translation, or deterministic editorial logic without benchmark evidence.

7. **FreeLLMAPI — development experiment only**
   - May be evaluated behind an isolated provider/test adapter for cheap stress tests, fallback experiments, or non-canonical development runs.
   - Never use free endpoints as the production reliability baseline for scheduled collection, private review, or channel-style translation.
   - Do not send private review data, secrets, cookies, or sensitive archives to unknown/free providers.
   - Any future provider promotion requires explicit reliability, privacy, quota, terms/license, observability, and quality review.

### Priority C — product ideas for later media stages

8. **AutoShorts — future optional media prototype**
   - Revisit only after the core source-completeness and media-download pipeline is stable.
   - Potential use: local long-form video/audio -> candidate vertical clips for private editorial review.
   - Keep clip generation opt-in and separate from collection truth, translation, and delivery state.
   - Require local-resource, FFmpeg, licensing, output-quality, and false-positive ranking benchmarks before adoption.

9. **OpenMontage — video-studio architecture inspiration / isolated sidecar candidate**
   - Treat primarily as inspiration for a future agentic video-production surface.
   - Because external copyleft/licensing boundaries may affect distribution, perform an explicit license review before reusing code.
   - Prefer a separate process/tool boundary rather than importing a video-production framework into the bot's core runtime.
   - No automatic public publishing.

### Priority D — deferred or no current fit

10. **9Drive — deferred storage architecture inspiration**
    - Do not integrate now.
    - Revisit only if the product later has a demonstrated requirement for multi-account cloud media/file storage that current GitHub/Telegram/local-state paths cannot satisfy.
    - Any future evaluation must include OAuth/credential isolation, quota routing, consistency, deletion, backup/recovery, privacy, and provider-lock-in analysis.

11. **AdGuard Home — no roadmap integration**
    - Network-wide ad/tracker blocking is outside this product's responsibility.
    - Do not add it to the bot, CI, runtime, or developer bootstrap unless a future, separately justified infrastructure requirement changes scope.

### Sequencing rule

For future broad `next stage` requests, external-tool work should follow this order when relevant:

`codebase-memory-mcp benchmark -> optional adoption -> Graft complementary benchmark -> selective research/agent tooling -> only then product-specific media/storage prototypes`.

Do not skip a higher-value product frontier merely to install tooling. Tool adoption is successful only when it measurably improves safe implementation, research quality, or maintenance without coupling production to the developer tool.

### Trendshift discovery & second-batch tooling review — 2026-09-25

Trendshift is a **discovery signal**, not an adoption authority. Future deep next-stage work may scan Trendshift's daily/weekly/monthly GitHub momentum lists for relevant projects, but every candidate must be verified against its upstream repository, license, current maintenance, security posture, and actual project fit before any code, skill, hook, MCP server, model, or installer is adopted.

12. **Browser Use — controlled research/browser sidecar; do not replace production collectors**
    - Useful for developer-driven web research, interactive-site inspection, and future browser/UI experiments.
    - Do not replace the existing source collectors, X recovery path, or deterministic offline fixtures with browser-agent navigation merely because it can click pages.
    - Production collection must not depend on CAPTCHAs, residential proxies, mutable browser profiles, or opaque hosted-agent behavior.
    - If evaluated, use a separate local/dev profile with no production cookies/secrets and record site/ToS/privacy/resource failure modes.

13. **AgentMemory (`rohitg00/agentmemory`) — high-priority developer-memory benchmark**
    - Evaluate as persistent coding-agent memory across sessions, but **not** as bot runtime memory.
    - Benchmark against the repository's existing Project Memory workflow and the planned codebase-memory-mcp evaluation rather than enabling multiple auto-capture systems at once.
    - Start with auto-capture/compression/hooks disabled or tightly scoped until exclusions are proven.
    - Use a repository-specific data directory/instance; never share this project's memory namespace with the Persian Literary Translation Engine.
    - Exclude secrets, X cookies, Telegram identifiers/tokens, private review/archive content, SQLite runtime state, encrypted backups, and generated production artifacts.
    - Adoption requires auditable recall/forget/export behavior, rollback, and evidence that retrieved memory improves later tasks rather than introducing stale decisions.

14. **Scientific Agent Skills — selective research skills only**
    - Do not install the full scientific catalog into the project.
    - Future research-heavy stages may selectively review general-purpose skills such as evidence retrieval, database lookup, literature/research workflow, statistics, or reproducible analysis when they directly support a concrete decision.
    - Biomedical/chemistry-specific skills are out of scope unless a future task actually requires them.
    - Any selected skill remains developer guidance and must not alter production bot behavior.

15. **Diagram Design — approved for developer/docs evaluation**
    - Useful for architecture maps, source/collection flows, recovery diagrams, trust boundaries, state machines, deployment views, database schemas, and user journeys.
    - Prefer self-contained static HTML/SVG artifacts for documentation and review.
    - Keep generated diagrams non-authoritative: contracts, tests, code, and normative Markdown remain the source of truth.
    - If vendored/installed, pin provenance/license and keep it outside production dependencies.

16. **Anthropic-Cybersecurity-Skills — defensive subset inspiration only**
    - This is a community project, **not an official Anthropic security package**.
    - Never bulk-install the full offensive/security catalog.
    - Only review defensive material relevant to this repository: GitHub Actions hardening, secret handling, dependency/supply-chain review, threat modeling, logging/privacy, incident response, web/API security where applicable.
    - Do not introduce offensive tooling, credential-extraction workflows, or unrelated pentest automation into the repository.

17. **Awesome Harness Engineering — high-value reference, not a dependency**
    - Use as a research index for context delivery, memory, safe autonomy, tool design, verification loops, observability, long-horizon task state, worktree/PR isolation, and human-in-the-loop patterns.
    - Periodically compare useful patterns against `docs/AGENT_NEXT_STAGE_PROTOCOL.md` and the project-next-stage skill.
    - Adopt only individual proven patterns; never copy a generic harness wholesale over project-specific safety rules.

18. **OpenViking — serious unified-context benchmark, external/dev-only initially**
    - Evaluate as a unified context layer for resources + coding memories + skills only after the lighter memory/context candidates have baseline results.
    - Main-project AGPL licensing makes embedding/distribution a separate legal/architecture decision; initial evaluation should remain an isolated local service/tool.
    - Use an independent instance/namespace for this repository.
    - Do not ingest private Telegram review/archive data, credentials, encrypted state, or unrelated repositories by default.
    - Compare retrieval quality, observability, token/context savings, stale-memory handling, deletion, resource cost, and operational complexity against AgentMemory + codebase-memory-mcp + existing Project Memory.
    - Do not run OpenViking, AgentMemory, Graft, and other memory systems simultaneously by default merely because they are available.

19. **KAT-Coder-Pro — optional developer-model benchmark only**
    - The exact label `KAT-Coder-Pro V9.5` was not verified during the 2026-09-25 review; verify the exact model identifier before any configuration change.
    - The publicly verified current candidate is KAT-Coder-Pro V2.5, a proprietary agentic coding model.
    - It may be A/B tested on bounded coding/repair tasks if a compatible provider is already available, but it is not a repository dependency and must not become a production bot provider.
    - Compare correctness, tests repaired, tool behavior, cost, latency, and regression rate against the currently used coding agent/model before retaining it.

20. **abi/screenshot-to-code — defer unless a web/dashboard product surface exists**
    - Current private Telegram UX does not justify integrating screenshot-to-code.
    - If a future web/dashboard surface is approved, it may be used as a developer prototype accelerator for reference screenshots/mockups.
    - Generated UI must be reviewed for originality, accessibility, RTL/mixed-script behavior, dependency/security quality, and consistency with the project's own product identity.
    - Never treat screenshot conversion as permission to clone copyrighted third-party UI exactly.

### Memory/context tool competition rule

AgentMemory, OpenViking, codebase-memory-mcp, Graft, and existing Project Memory overlap. Treat them as **benchmark competitors/complements**, not a shopping list.

A future stage should define one representative long-horizon task, measure baseline behavior, then test candidates one at a time. Retain the smallest combination that measurably improves correctness/context recovery while preserving isolation, auditability, privacy, rollback, and low maintenance overhead.

### Authoritative execution decision matrix

For future `next stage` work, use this matrix as the default action. Detailed safety notes above still apply.

| Candidate | Decision | Trigger / required future action |
|---|---|---|
| Trendshift | **ADOPT AS DISCOVERY PROCESS** | During substantial research/tooling stages, scan relevant trending GitHub projects, then independently verify upstream/license/security before proposing adoption. |
| codebase-memory-mcp | **BENCHMARK -> INSTALL DEVELOPER-SIDE IF IT WINS** | First context-tool benchmark. If it materially improves a representative deep repo task, configure it manually per-repository with disposable ignored indexes and no production/private state. |
| AgentMemory | **BENCHMARK -> INSTALL ONLY IF IT BEATS/COMPLEMENTS THE WINNER** | Test persistent cross-session coding recall after the baseline/context benchmark; keep auto-capture tightly scoped and project-isolated. |
| OpenViking | **BENCHMARK LATER -> OPTIONAL EXTERNAL SIDEcar** | Test only after lighter context/memory candidates. Retain only if unified resources/memories/skills gives clear gains worth the AGPL/ops complexity. |
| Graft | **BENCHMARK AFTER PRIMARY CONTEXT TOOL** | Keep only if it adds complementary context rather than duplicate/noisy retrieval. |
| PI-Desktop | **OPTIONAL EXTERNAL WORKSPACE** | May be installed locally for developer convenience; never add as bot/runtime/CI dependency. |
| Browser Use | **OPTIONAL DEV/RESEARCH INSTALL** | Use for interactive research or UI/site inspection; never replace deterministic X/source collectors or production retrieval paths. |
| Hyperresearch | **OPTIONAL RESEARCH SIDECAR** | Use on evidence-heavy architecture/provider/library investigations when its workflow materially improves source coverage/provenance. |
| Scientific Agent Skills | **SELECTIVE SKILL ADOPTION** | Install/vendor only the specific general research/evidence/statistics skill needed for a concrete stage; never bulk-install the catalog. |
| Diagram Design | **ADOPT FOR DOCS WHEN NEEDED** | Use for architecture/data-flow/threat-model/state-machine documentation; pin provenance if vendored and keep output non-authoritative. |
| Anthropic-Cybersecurity-Skills | **INSPIRATION / SELECTIVE DEFENSIVE EXTRACTION** | Review only defensive skills relevant to CI, secrets, supply chain, web/API security, threat modeling and incident response; do not bulk-install. |
| Awesome Harness Engineering | **INSPIRATION / PERIODIC HARNESS REVIEW** | Mine individual patterns for context delivery, verification loops, observability, safe autonomy and long-horizon agent work; adapt them into project-native protocols. |
| Agency-agents | **INSPIRATION / SELECTIVE ROLE EXTRACTION** | Review only a narrowly useful specialist role and rewrite it into project-specific guidance after conflict/license review. |
| KAT-Coder-Pro | **OPTIONAL MODEL A/B TEST** | Verify the exact current model ID first; use only for bounded coding tasks and retain only if it improves correctness/tests/cost/latency versus baseline. |
| Needle | **DEFERRED PROTOTYPE** | Evaluate only if a concrete offline structured-extraction/tool-routing gap appears. |
| FreeLLMAPI | **DEV/TEST ONLY** | May be used behind an isolated non-canonical test adapter; never production reliability baseline and never receive private state/secrets. |
| AutoShorts | **FUTURE MEDIA PROTOTYPE** | Revisit only after source completeness/media retrieval is stable and the admin explicitly wants short-form clip generation. |
| OpenMontage | **INSPIRATION / FUTURE ISOLATED VIDEO SIDECAR** | Revisit only for an approved video-production scope; perform license review first and keep away from core runtime. |
| abi/screenshot-to-code | **DEFER UNTIL A WEB/DASHBOARD SURFACE EXISTS** | Use only as a prototype accelerator for project-owned/approved references, followed by accessibility/RTL/originality/security review. |
| 9Drive | **DEFERRED STORAGE INSPIRATION** | Revisit only after a demonstrated multi-account cloud-storage requirement exists. |
| AdGuard Home | **OUT OF SCOPE** | Do not install or integrate unless product scope changes for a separately justified infrastructure reason. |

#### Default future sequence

1. Do not install multiple context/memory systems together.
2. Benchmark `codebase-memory-mcp` first against the current Project Memory baseline.
3. Benchmark AgentMemory and then Graft/OpenViking only if the first result leaves a demonstrated gap.
4. Retain the **smallest** context stack that wins on correctness, stale-context resistance, privacy, rollback, maintenance cost, and deep-task efficiency.
5. Install/use research and documentation helpers only at stages that actually need them.
6. Media/storage tools wait for their product feature trigger; they are not prerequisites for current roadmap progress.

