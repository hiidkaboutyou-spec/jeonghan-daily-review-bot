---
name: verified-agent-orchestration
description: Evidence-bound planning, execution, tool provenance, and completion rules for long-running or external-tool-assisted engineering work.
---

# Verified Agent Orchestration

Use this skill together with `agent-session-safety` for scheduled/hourly runs, broad next-stage work, multi-step agent execution, or decisions about adding external agent/MCP/developer tools.

This is developer infrastructure. It must never become a production bot dependency or a new authority over Telegram/X/archive/runtime state.

## 1. Separate planning from mutation

For work with material architectural, dependency, security, privacy, or production impact:

1. inspect current repository/GitHub truth;
2. define the owner-visible outcome;
3. build a decision-complete checklist with acceptance evidence and rollback;
4. only then enter the mutation phase.

Research/planning may fan out across independent read-only questions. Do not let exploratory workers mutate the repository or production state.

## 2. Keep a resumable execution receipt

Long work must leave a compact durable receipt in the repository's existing handoff/project-memory surface. Record only:

- user-visible goal;
- canonical main SHA used for the run;
- active branch/PR and prerequisites;
- checklist items completed vs open;
- strongest evidence for each completed item;
- blocker/rollback/next action.

Do not introduce a second runtime state store merely for agent progress. Never put private Telegram content, cookies, chat IDs, secrets, decrypted backups, or user data in the receipt.

## 3. Completion requires evidence, not a status string

An agent saying "done" is not completion evidence.

Before closing substantive work:
- rerun the acceptance evidence named before implementation;
- inspect the returned result, not merely that a workflow started;
- use an independent convergence/adversarial pass for high-impact changes;
- distinguish implemented, PR-head verified, merged, post-merge verified, and owner-visible confirmed.

If evidence fails, continue from the first failed/unproven item rather than restarting or stacking speculative fixes.

## 4. Risk-classify every execution

Reuse the repository's session-safety action classes:

- read-only;
- repository write;
- external side effect;
- destructive/irreversible.

Repository writes require target/branch/path verification. External or destructive actions require explicit authorization and project acceptance rules. A "bypass" or autonomous mode must never silently bypass dangerous/destructive safeguards.

## 5. External tool and MCP provenance gate

Before adding or connecting a developer tool, MCP server, agent harness, knowledge base, or telemetry/eval platform, record:

- the measured gap it solves;
- adopt / adapt-pattern / reject / defer decision;
- upstream owner and exact canonical source;
- maintenance/activity and version/pin strategy;
- license and transitive-license impact;
- authentication, data scope, network/telemetry behavior;
- secret/private-data exposure risk;
- runtime coupling and rollback/removal path.

Prefer first-party/official provider servers and official registries when an MCP integration is needed. A curated list is discovery evidence, not permission to install blindly.

Do not add a second orchestration harness when the repository already has equivalent project memory, planning, safety, and verification contracts unless a measured missing capability justifies the overlap.

## 6. Trace and evaluation boundary

Production traces may become engineering evidence only when they are intentionally privacy-safe and bounded.

- Never export raw private Telegram messages, cookies, identifiers, decrypted state, or secrets to an external training/eval service.
- Prefer local/synthetic regressions and explicitly confirmed review edits.
- Human preference pairs are evaluation/training candidates only after explicit eligibility filtering.
- No production auto-training or model replacement may occur from collected traces.
- Any future fine-tuning requires a separate benchmark, privacy/license review, rollback path, and production-admission decision.

## 7. Local-first developer tools stay optional

Local agent shells, knowledge bases, or remote-control layers may assist development, but:
- GitHub Actions remains the production automation authority unless a separately reviewed migration changes that;
- external developer stores must not become bot archive/cursor/delivery state authorities;
- local tooling must not be required for CI or normal production runtime;
- failure/unavailability of optional developer tooling must not break the bot.

## Provenance

This contract independently adapts useful public patterns observed on 2026-10-05 from:
- `code-yeongyu/lazycodex`: plan/execute separation, resumable progress, evidence-gated completion;
- `avibe-bot/avibe`: local-first sessions/tasks/watch concepts and durable task history;
- `ys-ll/uniterm`: explicit read/write/dangerous execution classification and auditability;
- `overmind-core/overmind`: traces -> datasets -> evaluations before training;
- `VoltAgent/official-mcp-servers`: prefer official first-party MCP provenance.

No upstream implementation code is vendored by this skill.
