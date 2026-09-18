# GitHub capability scan for Daily Hani — 2026-09-18

## Goal

Evaluate external GitHub projects only where they can close a concrete reliability, security, recovery, media, or translation-quality gap without replacing the working Daily Hani architecture.

## Adopt now — CI only

### zizmor
Repository: `zizmorcore/zizmor`

Use: static security analysis of GitHub Actions for template injection, excessive permissions, credential persistence/leakage, suspicious refs and other workflow-specific risks.

Decision: add in a separate CI-only PR, pinned to a known release/commit. Start report-only and promote high-confidence findings to blocking only after triage. Never add it to production requirements.

### actionlint
Repository: `rhysd/actionlint`

Use: workflow syntax/expression/action-input/reusable-workflow/cron validation plus shellcheck integration.

Decision: add in the same separate CI-only PR. Keep it blocking for workflow correctness. Install only inside the CI job at pinned version `v1.7.12`; do not add it to production requirements.

## Adapt patterns — do not install wholesale

### x-tweet-fetcher
Repository: `ythx-101/x-tweet-fetcher`

Useful patterns:
- captured provider fixtures;
- parser regression tests against real response structures;
- explicit backend capability/routing contracts;
- incremental fetch ledger concepts.

Decision: do not introduce a second X collector stack. Add captured fixtures/capability contracts later around Hani's existing twscrape/syndication/FxTwitter/Agent-Reach chain.

### sqlite-checkpoint
Repository: `GreyforgeLabs/sqlite-checkpoint`

Useful pattern: SQLite online backup API rather than relying on raw file reads after an external WAL checkpoint.

Decision: do not add a runtime package yet. Add a later hardening task to snapshot the private SQLite DB via Python's stdlib `sqlite3.Connection.backup()` into a temporary consistent DB, validate it, then encrypt that snapshot. This reduces coupling between backup correctness and workflow step ordering.

### durable-agents
Repository: `AleBrito124356/durable-agents`

Useful patterns:
- deterministic idempotency keys;
- once-ledger;
- bounded retries/dead-letter;
- visibility timeout/heartbeat;
- crash-safe step checkpoints.

Decision: reference patterns only. Hani already has purpose-built SQLite/state and Telegram idempotency; installing an agent/job framework would duplicate authority.

### python-telegram-bot
Useful pattern: two-level Telegram rate limiting and RetryAfter handling.

Decision: do not replace Hani's custom transport. Compare its limiter behavior when Telegram transport is next revised.

## Later experimental tracks

### MQM-lite translation QA
Reference: Google WMT MQM human-evaluation methodology.

Build a Daily-Hani-specific deterministic error taxonomy over the existing benchmark:
- omission/addition;
- entity/speaker;
- number/date;
- tone/register;
- emoji/laughter;
- relationship/fandom nuance.

This is a benchmark/evaluation layer, not a runtime translation dependency.

### Perceptual media dedupe
References: ImageHash/dHash/imagededup families.

Hani already has exact URL/byte/file_unique_id identities. A later feature-flagged advisory layer may compute perceptual hashes to group resized/recompressed near-duplicates. It must **not suppress media automatically** until false-positive behavior is calibrated on real channel assets.

### Text near-dedup
Reference: text-dedup / SimHash/MinHash families.

Only consider if duplicate-caption volume becomes a measurable problem. Initial use should be grouping/advisory, not destructive suppression.

## Rejected for now

- full replacement Telegram framework;
- Redis/Celery or a second durable queue authority;
- a second full X collector stack;
- full Agent Reach system installation beyond the already bounded audited X fallback;
- heavy COMET/xCOMET models in production;
- full image/text-dedup frameworks in production.

These add overlapping authority, supply-chain surface, or runtime complexity without a currently demonstrated gap.
