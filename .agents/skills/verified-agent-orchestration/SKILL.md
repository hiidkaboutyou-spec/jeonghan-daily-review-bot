---
name: verified-agent-orchestration
description: Evidence-bound planning, agent governance, execution, external-tool admission, memory/decision-model boundaries, and verified completion for long-running engineering work.
---

# Verified Agent Orchestration

Use this skill together with `agent-session-safety` for scheduled/hourly runs, broad next-stage work, multi-step agent execution, browser-assisted engineering, or decisions about adding agent/MCP/memory/decision tooling.

This is developer infrastructure. It must never become a production bot dependency or a second authority over Telegram, X, archive, cursor, delivery, or private-review state.

## 1. Separate planning from mutation

For work with material architecture, dependency, security, privacy, cost, or production impact:

1. recover current repository/GitHub truth;
2. define the owner-visible outcome;
3. define acceptance evidence, rollback, and a bounded work budget;
4. only then mutate.

Read-only exploration may fan out. Exploratory workers must not mutate the repository or production state.

## 2. Assignment, role, and authority are separate

An agent role, title, wake/heartbeat, mention, assignment, or prior successful run is not permission to perform a write or external side effect.

Before mutation verify:
- the current task/goal still owns the work;
- the actor has the needed repository/product authority;
- the target branch/path/system is correct;
- any human-only or approval-gated step remains gated.

Never delegate or switch actors merely to bypass a denied capability. If blocked, preserve the blocker and continue only when the missing authority/capability is actually resolved.

## 3. Bounded budgets and stop conditions

Long-running work must declare bounded dimensions appropriate to the task, such as:
- implementation/retry attempts;
- model/provider calls;
- live browser/external calls;
- parallel workers;
- changed surfaces/PR scope.

When a soft budget is nearly exhausted, narrow to the critical acceptance path. At the hard budget, stop new speculative work, preserve evidence, and hand off the exact blocker. Do not burn additional calls merely to make a run look active.

The existing three-materially-different-fix-attempt rule remains authoritative for repeated failed fixes.

## 4. Unknown side effects are not retryable facts

For repository writes, provider calls, Telegram sends, browser mutations, deployments, releases, or other external effects:

- log/record intent before execution when the path supports it;
- inspect the returned receipt/outcome afterward;
- if the outcome is unknown, do not speculate that it failed;
- never replay a mutation only because its result is uncertain;
- reconcile/fence the previous attempt first, or hold for review.

A replayable evidence record must be side-effect free. Replaying evidence must never replay the original mutation.

## 5. Browser-assisted work uses observed actions

If future source recovery, UI QA, or engineering uses browser automation:

- derive actions only from current observed controls/state;
- restrict choices to supported operation types and compatible observed targets;
- never let model output become arbitrary selectors, coordinates, shell commands, or executable JavaScript;
- re-check target freshness/visibility before mutation;
- never retry an uncertain browser mutation;
- require an independent outcome verifier; an agent/model choosing `DONE` is not success evidence;
- keep credentials/private data out of generic traces.

Jev-style fast decision models may be benchmarked, but paid/browser decision providers are never required for normal bot operation.

## 6. Decision-model admission gate

Fast typed-decision models such as Laya are **research candidates**, not production dependencies.

Before admission:
1. define the exact bounded decision gap (for example relevance/content routing);
2. build a project-owned labeled benchmark from rights-safe/synthetic or explicitly eligible data;
3. compare against the current deterministic/current-provider baseline;
4. measure per-class errors, false-negative/false-positive cost, calibration, abstention coverage, latency, memory and cold-start;
5. require a fail-closed abstention path for low confidence;
6. review checkpoint/model/data license and download/supply-chain behavior;
7. keep model downloads out of default CI and production unless separately approved.

No decision model may advance X cursors, publish/send content, alter archive authority, or mark translation quality accepted merely from its confidence score.

## 7. Memory-system admission gate

Persistent-memory systems such as Hindsight may be explored only in shadow/read-only evaluation first.

- Existing private archive, final-edit evidence, project memory, and repository/GitHub truth remain authoritative.
- External memory classes such as world/experience/observation are retrieval labels, not authority upgrades.
- Inferred/reflective opinions never become user preference or factual truth automatically.
- Benchmark multi-arm retrieval (semantic/text/graph/temporal) against an existing labeled retrieval task before adoption.
- Measure recall/precision/ranking, latency, storage growth, stale-memory behavior and deletion/revocation.
- Never send raw private Telegram content, identifiers, cookies, secrets, or decrypted state to a hosted memory service by default.
- An optional local memory service must fail open to the existing bot behavior and must not become required CI/runtime infrastructure.

## 8. Sandbox/orchestrator admission gate

Large agent control planes such as Paperclip or AX may help developer operations, but they must not become production bot infrastructure without a measured need.

If evaluating one, require:
- isolated workspace and explicit filesystem/network scope;
- suspend/resume or checkpoint semantics that preserve idempotency;
- resource/cost limits and hard stops;
- approval boundaries and auditable mutations;
- one authoritative task/state owner;
- rollback that returns GitHub Actions + repository state to sole authority.

Do not introduce Kubernetes/cluster infrastructure merely to run the existing hourly repository workflow.

## 9. Completion requires independent evidence

An agent saying "done" is not completion evidence.

Before closing substantive work:
- rerun the acceptance evidence named before implementation;
- inspect the actual returned result, not merely that a workflow started;
- perform an independent convergence/adversarial pass for high-impact changes;
- distinguish implemented, PR-head verified, merged, post-merge verified, and owner-visible confirmed.

## 10. External tool/MCP provenance gate

Before adding or connecting a developer tool, MCP server, agent harness, knowledge base, telemetry/eval platform, browser agent, or model:
- record the measured gap;
- choose adopt / adapt-pattern / reject / defer;
- record canonical upstream owner/source, maintenance state and version/pin strategy;
- review license/transitive license, model/data rights, authentication, data scope, network/telemetry, secrets, supply-chain/download behavior, runtime coupling, and rollback.

Prefer first-party/official provider servers when MCP is needed. Discovery is not installation approval.

## Provenance

This contract independently adapts public patterns reviewed on 2026-10-05 from:
- `paperclipai/paperclip`: explicit authority, approvals, budget hard stops, heartbeat/task discipline, audit receipts, and conservative no-replay semantics;
- `browser-use/jev-ultrafast`: observed indexed actions, freshness guards, no mutation retry, and independent final verification;
- `NandhaKishorM/laya`: typed decisions, calibrated confidence and abstention as benchmarkable routing primitives;
- `google/ax`: sandbox/workspace isolation, resource fencing, suspend/resume;
- `vectorize-io/hindsight`: typed long-term memory and multi-arm semantic/text/graph/temporal recall;
- prior reviewed LazyCodex, Avibe, uniTerm, Overmind and official-MCP patterns.

No upstream implementation code or model weights are vendored by this skill.
