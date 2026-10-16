# External agent/tooling adoption review — 2026-10-05

Scope: evaluate the previously supplied repositories/resources for the Jeonghan Daily Review Bot without adding tools merely for feature count.

## Decision matrix

| Source | Useful pattern for this repository | Decision | Why / boundary |
| --- | --- | --- | --- |
| `code-yeongyu/lazycodex` | plan-before-edit, durable progress, separate verification of completion | **Adapt pattern** | This repository already has `AGENTS.md`, project memory, evidence-first, next-stage, production-acceptance, and session-safety contracts. Installing a second harness would duplicate orchestration and can create conflicting hooks/state. |
| `avibe-bot/avibe` | local-first durable sessions/tasks/watches; asynchronous dispatch/history | **Defer integration; adapt operational ideas only** | Useful for a developer workstation, not for the production bot. Production remains GitHub Actions + existing Telegram runtime. |
| `nzbdav-dev/nzbdav` | streaming/seek/cache ideas | **Reject for current scope** | It solves NZB/WebDAV media-library access, not X/Telegram/AO3 ingestion, and upstream is no longer maintained. |
| `tianma-if/edgeever` | developer knowledge base, MCP CRUD, revision history | **Defer / optional developer-only** | It must never replace bot archive/state or project memory. AGPL-3.0 makes code vendoring unattractive when existing memory contracts already cover the need. |
| `ys-ll/uniterm` | read/write/dangerous execution classes; audit log; bounded command execution | **Adapt pattern** | Useful safety semantics without a terminal dependency in CI/runtime. |
| `overmind-core/overmind` | privacy-safe traces -> datasets -> evaluations -> optional training | **Adapt evaluation lifecycle only** | Keep data local/private; no telemetry, auto-training, or model switch. |
| `yihui-dev/awesome-opus5-5-videos` / osp.fyi | motion-graphics/explainer prompt examples | **Defer to future UI/demo work** | No direct production reliability or collection benefit. |
| `VoltAgent/official-mcp-servers` | official/first-party MCP discovery and provenance | **Adopt as selection policy** | Official ownership, permissions, data scope, license, network behavior and rollback are mandatory checks; directory presence is not install approval. |

No runtime dependency was added from this batch.
