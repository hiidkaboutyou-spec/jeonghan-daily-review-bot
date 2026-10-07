# External source adoption audit — 2026-10-08

## Scope

Reviewed against Hani's current Python/Telegram/X/AO3 runtime and repository-owned production gates:

- awesome-selfhosted/awesome-selfhosted
- trimstray/the-book-of-secret-knowledge
- avelino/awesome-go
- Solido/awesome-flutter
- OpenMinis/OpenMinis release 0-beta-build32
- Context7, Firecrawl and PostHog developer tooling

The rule is implementation-first: use only a component or idea that closes a demonstrated Hani gap without weakening source completeness, durable state, private review, Telegram idempotency, privacy or production recovery.

## Decisions

### Awesome Selfhosted — discovery only

The catalog is useful for monitoring/status, backup, analytics, automation and search candidates. Hani already has repository-owned encrypted recovery, `/healthz`, structured logging/Sentry, workflows and state ownership. No additional self-hosted service is justified by a measured gap in this slice.

If an external monitor is considered later, it should consume only the content-free health contract and must not gain access to Telegram/X credentials or private queue/state content.

### The Book of Secret Knowledge — adapted into the operations runbook

Useful Linux/network/debugging techniques were converted into a conservative incident section: project-owned checks first; read-only process/listener/disk inspection second; packet/process tracing explicitly non-default because it can capture secrets and private content. No upstream script or destructive command is vendored or automated.

### Awesome Go — defer direct adoption

Hani is Python 3.11 and its runtime boundaries are already explicit. A Go sidecar would add packaging, IPC and operational authority without a measured latency/reliability gain. The catalog remains a discovery source if a future independently benchmarked component genuinely needs a single-binary Go service.

### Awesome Flutter — not applicable to Hani runtime

Hani is a Telegram assistant, not a Flutter client. No Flutter dependency or UI runtime is added.

### OpenMinis 0-beta-build32 — clean-room long-run engineering patterns

The 2026-10-07 beta release includes incremental trimming of old reasoning/tool output, steer/stop for sub-agents, bounded parallelism, run-now scheduled tasks, MCP/skill controls, per-provider response timeouts, provider-disable fallback, tool-result ordering after compaction, orphan-session cleanup and crash recovery.

Useful patterns are adapted into `.agents/skills/agent-session-safety/SKILL.md`: incremental context reduction; exact evidence retention; one mutation authority per branch/integration path; explicit stop/steer handoff; resume from current GitHub truth. Hani already has bounded network timeouts and explicit provider/recovery paths, so no second scheduler/provider runtime is added.

OpenMinis is GPL-3.0. No implementation code was copied.

### Context7 — developer research only

Used to verify current SDK/library behavior when API freshness matters. It is not a Hani runtime dependency and must not receive credentials, cookies, private Telegram content or private review data.

### Firecrawl — developer/upstream research only

Used for current repository/release/implementation evidence. It is not inserted into Hani's source collectors merely because it can crawl the web. Any future source-provider use needs separate completeness, anti-abuse, privacy, rate-limit and cursor/checkpoint evidence.

### PostHog — current guidance reviewed; runtime SDK deferred

PostHog's current Python SDK can operate disabled/no-op and supports data-collection controls. Hani nevertheless already owns content-safe structured logs and optional scrubbed Sentry. Adding PostHog now would duplicate observability and create a new telemetry/network/privacy surface without a measured question it uniquely answers.

If product analytics later becomes necessary, only allowlisted technical aggregates may be considered; no post text, captions, URLs, chat/user identifiers, credentials, cookies, request bodies or private review content may be captured. Self-hosting does not remove the need for this data-minimization boundary.

## Permanent adoption rule

Awesome-lists are candidate indexes, not install manifests. Every external candidate needs: concrete gap; current upstream evidence; exact license; maintenance signal; Python 3.11/runner compatibility; privacy/network/secret analysis; failure behavior; benchmark/regression evidence; rollback; exact-head CI and production acceptance when runtime behavior changes.

## Rollback

This slice changes agent-engineering guidance and operations documentation only. Revert the skill/runbook/note; no production package, source authority, cursor, state schema, delivery behavior, secret, model or schedule changes.
