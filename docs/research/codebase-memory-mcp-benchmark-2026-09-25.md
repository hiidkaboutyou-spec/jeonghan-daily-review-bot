# codebase-memory-mcp isolated benchmark gate — 2026-09-25

## Status

Research/benchmark gate only. No production/runtime dependency is added by this document.

Canonical base inspected before this work:

`main@0e3384bee94043d159c158bd7dbcdcdc86db4fdd`

The repository's current external-tooling roadmap explicitly puts `codebase-memory-mcp` first in the context/memory competition and requires a benchmark before installation/adoption.

## Why this is the next safe tooling step

The project now has multiple overlapping context candidates (Project Memory, codebase-memory-mcp, AgentMemory, Graft, OpenViking). Installing them together would make attribution, rollback, privacy, stale-context analysis, and maintenance cost impossible to measure cleanly.

Therefore this stage evaluates exactly one candidate first and keeps product/runtime work independent.

## Upstream reviewed

Candidate: `DeusData/codebase-memory-mcp`

Observed upstream properties on 2026-09-25:

- MIT license.
- Active upstream maintenance through 2026-09-24.
- Native local code-intelligence/MCP tool; no model/API key required for indexing/querying.
- Persistent SQLite-backed graph/cache.
- Supports Python and Rust, both relevant to this repository.
- Upstream documents SLSA provenance, Sigstore/cosign, checksums, SBOM, CodeQL and release scanning.
- Upstream explicitly states that it reads project files, writes agent configuration during normal install, and can spawn a background daemon/watcher.
- Runtime indexing/querying is documented as local; an asynchronous GitHub release-metadata update check exists.
- `CBM_ALLOWED_ROOT` can confine indexing to an explicit repository root.
- `CBM_CACHE_DIR` can isolate graph/cache state.
- `watcher_enabled=false` disables the background watcher.
- `auto_index` defaults false.
- `install --dry-run` is documented for inspecting integration/config writes.
- The shell installer supports `--skip-config`, avoiding automatic agent configuration during an isolated binary-only evaluation.

Upstream references reviewed:

- https://github.com/DeusData/codebase-memory-mcp
- https://github.com/DeusData/codebase-memory-mcp/blob/main/LICENSE
- https://github.com/DeusData/codebase-memory-mcp/blob/main/SECURITY.md
- https://github.com/DeusData/codebase-memory-mcp/blob/main/docs/CONFIGURATION.md
- https://github.com/DeusData/codebase-memory-mcp/blob/main/install.sh

## Security decision

Do **not** run a piped `curl | bash` installer for this project benchmark.

Do **not** let the first evaluation rewrite Codex/Claude/editor MCP configuration or project instructions.

Do **not** enable background watching, cross-repository indexing, diagnostics, UI, or automatic indexing during the first benchmark.

Do **not** index:

- runtime SQLite/state;
- encrypted backups;
- Telegram/X secrets or cookies;
- private review/archive exports;
- generated artifacts containing private content;
- unrelated repositories;
- the Persian Literary Translation Engine.

The first benchmark must use a disposable cache and an explicit allowed root. Any later install must verify the exact release artifact/provenance/checksum before execution.

## Benchmark protocol

### A. Baseline

Use one representative maintenance question that requires non-trivial repository navigation, for example:

> Trace the complete production path from a scheduled Daily trigger through source retrieval, recovery/completeness state, private-review translation/style layers, Telegram delivery, and watchdog recovery. Identify the exact modules/workflows that own each boundary and list regression tests protecting them.

Run it using the current project workflow without codebase-memory-mcp.

Record:

- files/tool calls needed;
- wrong/dead-end files opened;
- missed architecture boundaries;
- incorrect/stale claims;
- time-to-evidence;
- context/token burden when observable;
- whether the answer cites exact code/tests.

### B. Isolated candidate run

Before any configuration write:

1. inspect the exact release and release notes;
2. verify provenance/checksum/signature for the downloaded artifact;
3. use binary-only/no-agent-config setup;
4. set a repository-specific disposable `CBM_CACHE_DIR`;
5. set `CBM_ALLOWED_ROOT` to this repository only;
6. set `watcher_enabled=false`;
7. keep `auto_index=false`;
8. do not enable diagnostics or UI;
9. index only this repository;
10. rerun the exact same maintenance question.

Record the same metrics plus:

- indexing time;
- index size;
- peak/steady resource cost when observable;
- false/missing call edges;
- stale-index behavior after a small branch-only change;
- whether deletion of the disposable cache fully removes benchmark state.

### C. Safety/rollback proof

Before adoption, prove all of these:

- ordinary repository tests/CI do not depend on CBM;
- production workflows do not reference CBM;
- no tracked file changes are required merely to use CBM;
- uninstall/removal leaves the repository unchanged;
- deleting the disposable cache removes its project index;
- disabling/removing CBM does not affect Daily, Fanfic, Telegram, X recovery, or watchdog behavior;
- no private state was indexed.

## Acceptance criteria

Adopt developer-side only if the candidate shows a material improvement on the representative task without reducing correctness.

Minimum qualitative bar:

1. architecture/navigation answer is at least as correct as baseline;
2. no important production boundary is missed that baseline found;
3. materially fewer file/tool reads **or** materially faster evidence gathering;
4. no private/out-of-root indexing;
5. no required production/CI dependency;
6. rollback is clean and documented;
7. stale-index behavior is understandable and recoverable;
8. maintenance/resource cost is acceptable on the developer machine.

If the result is neutral or worse, reject/remove it and keep current Project Memory as the baseline before testing AgentMemory.

## Relationship to other candidates

Do not benchmark AgentMemory, Graft, or OpenViking simultaneously.

Sequence remains:

`current Project Memory baseline -> codebase-memory-mcp -> AgentMemory only if a persistent-memory gap remains -> Graft/OpenViking only for demonstrated complementary gaps`.

Browser Use, Scientific Agent Skills, Diagram Design, security-skill collections, KAT-Coder-Pro, and screenshot-to-code remain governed by the existing roadmap triggers and are not prerequisites for this benchmark.

## Existing project frontier discovered during preflight

Open PR #119 (Stage D Fanfic/AO3 isolation pilot) is currently stale relative to canonical main: its branch is 8 commits ahead and 11 commits behind `main`, with merge base `21a3502c6455c5b2c2829c305397b1b83fe33b45`.

Therefore:

- do not merge #119 in its current state;
- do not treat its old PR-head evidence as proof against current main;
- if Stage D Fanfic isolation is resumed, refresh it onto current canonical main and repeat exact-head Maintenance, Nightly Fanfic, Security/Workflow Safety, normal Daily and Watchdog evidence before any promotion.

Older open PRs are not automatically considered current roadmap work merely because they remain open.

## Next action after this document

Run the isolated local benchmark above on the developer machine where the repository and coding-agent configuration are available.

- If it wins: document exact version/hash, isolation settings, install/uninstall procedure and measured result; then adopt it developer-side only.
- If it loses: document rejection evidence, remove its cache/binary/config, and move to the next demonstrated context gap rather than stacking tools.
- Product/runtime roadmap work continues independently; no tooling benchmark may block a higher-value production repair.
