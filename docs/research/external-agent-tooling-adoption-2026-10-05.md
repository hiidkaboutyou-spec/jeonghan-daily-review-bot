# External agent/tooling adoption review — 2026-10-05

Scope: evaluate the user-supplied repositories/resources for the Jeonghan Daily Review Bot without adding tools merely for feature count.

## Decision matrix

| Source | Useful pattern for this repository | Decision | Why / boundary |
| --- | --- | --- | --- |
| `code-yeongyu/lazycodex` | plan-before-edit, durable progress, separate verification of completion | **Adapt pattern** | This repository already has `AGENTS.md`, project memory, evidence-first, next-stage, production-acceptance, and session-safety contracts. Installing a second harness would duplicate orchestration and can create conflicting hooks/state. No runtime dependency added. |
| `avibe-bot/avibe` | local-first durable sessions/tasks/watches; asynchronous dispatch/history | **Defer integration; adapt operational ideas only** | Useful for a developer workstation, not for the production bot. Production remains GitHub Actions + existing Telegram runtime. No second scheduler/state authority is introduced. |
| `nzbdav-dev/nzbdav` | streaming/seek/cache ideas | **Reject for current scope** | It solves NZB/WebDAV media-library access, not X/Telegram/AO3 ingestion. Upstream also declares the project no longer maintained. Adding it would increase attack/dependency surface without solving a measured bot gap. |
| `tianma-if/edgeever` | developer knowledge base, MCP CRUD, revision history | **Defer / optional developer-only** | Could hold engineering notes, but must never replace bot archive/state or project memory. AGPL-3.0 makes code vendoring especially unattractive when the existing repository memory contract already works. |
| `ys-ll/uniterm` | read/write/dangerous execution classes; audit log; bounded command execution | **Adapt pattern** | The risk model strengthens existing session-safety without requiring terminal software in CI/runtime. No dependency added. |
| `overmind-core/overmind` | privacy-safe traces -> datasets -> evaluations -> optional training | **Adapt evaluation lifecycle only** | The server is largely AGPL-3.0 while its SDK/CLI subtree is MIT. The bot already has a confirmed-edit preference export track (#172). Keep data local/private and do not add telemetry, auto-training, or a production model switch. |
| `yihui-dev/awesome-opus5-5-videos` / osp.fyi | prompt examples for motion graphics/explainers | **Defer to future UI/marketing/demo work** | No direct reliability, collection, translation, or Telegram benefit. MIT examples may inspire future visual demos, but they do not belong in bot runtime. |
| `VoltAgent/official-mcp-servers` | official/first-party MCP discovery and provenance | **Adopt as selection policy, not dependency** | Treat official ownership, permissions, data scope, license and rollback as mandatory checks. Do not install a server merely because it appears in a directory. |

## Concrete change

A repository-owned `verified-agent-orchestration` skill is added. It complements—not replaces—existing evidence-first and session-safety rules with:
- planning/mutation separation;
- resumable execution receipts using existing handoff surfaces;
- evidence-bound completion;
- risk-classified actions;
- MCP/tool provenance review;
- privacy-safe trace/eval boundaries.

## Explicit non-changes

- No bot runtime package added.
- No new scheduler, database, state authority, telemetry collector, model trainer, MCP server, terminal, or knowledge-base dependency.
- No secrets/private Telegram content leave the existing boundaries.
- No production workflow, source collector, Telegram delivery path, or translation provider is changed.

## Revisit criteria

Re-evaluate a direct integration only after a concrete measured gap exists and the candidate beats the existing path on reliability/latency/quality with reviewed license, security, privacy, maintenance, and rollback evidence.
