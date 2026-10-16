# Agent governance, decision, browser, and memory adoption review — 2026-10-05

Scope: evaluate Paperclip, Jev Ultrafast, Laya, Google AX, and Hindsight for the Jeonghan Daily Review Bot against current production architecture and the existing evidence/session-safety harness.

## Decisions

| Source | Useful parts | Decision for Daily | Reason |
| --- | --- | --- | --- |
| `paperclipai/paperclip` (MIT) | assignment/authority separation, approvals, budgets/hard stops, heartbeats, immutable audit evidence, conservative no-replay of unknown effects | **Adapt strongly** | These directly improve scheduled engineering-agent safety. Do not install Paperclip as a second scheduler/control plane; GitHub Actions and repository state remain authoritative. |
| `browser-use/jev-ultrafast` (MIT) | observed indexed controls, operation-specific targets, freshness/occlusion checks, never retry browser mutations, independent outcome verifier | **Adapt contract; defer runtime** | Useful for future browser-based source recovery/UI QA. Current implementation needs TypeSafe + a text model key and has important DOM limits; it is not a collection backend replacement. |
| `NandhaKishorM/laya` (Apache-2.0 code) | multilingual typed choice/score/yes-no, confidence, calibration, abstention, Apple-Silicon support | **Benchmark candidate only** | Base package adds Torch/Transformers/Safetensors/Hugging Face checkpoint downloads. Daily has no proven routing gap large enough to justify this production cost yet. Admit only if a labeled benchmark beats current routing with safe abstention. |
| `google/ax` (Apache-2.0) | isolated workspaces, resource/network fences, suspend/resume | **Adapt concepts; reject direct install** | AX is v1alpha/heavy-development and requires Kubernetes + Agent Substrate. This is disproportionate for the current GitHub-Actions production topology. |
| `vectorize-io/hindsight` (MIT) | world/experience/observation separation, retain/recall/reflect, semantic + BM25 + graph + temporal retrieval, reranking | **Shadow benchmark only** | Could improve developer/private preference retrieval, but existing archive/final-edit/project-memory authorities must not be duplicated. Hosted memory is privacy-sensitive; local service also adds storage/model complexity. |
 
## Paperclip patterns adopted

1. Role/title/wake/assignment never implies permission.
2. Approval-gated actions stay approval-gated; delegation cannot bypass denial.
3. Scheduled runs use bounded budgets and stop conditions.
4. Unknown mutation results are reconciled/fenced before any retry.
5. Audit/replay evidence is side-effect-free; replaying evidence never repeats the write.

## Jev patterns adopted

Future browser automation must operate only on observed current controls with bounded operation types, validate freshness before input, refuse speculative mutation replay, and verify the final outcome independently. A `DONE` model output is only a proposal.

No Jev/TypeSafe key, Browser Harness, or runtime dependency is added.

## Laya evaluation boundary

Potential Daily targets are limited to cheap shadow decisions such as relevance/content routing. Before any production proposal, compare Laya against the current baseline on a project-owned labeled corpus, including per-class errors, confidence calibration, abstention/selective accuracy, cold-start, steady-state latency, RAM, and failure behavior.

Do not download checkpoints in normal CI and do not let a learned router own cursor advancement, delivery, publication, or quality acceptance.

## Hindsight evaluation boundary

Hindsight's most interesting idea for Daily is not its database; it is multi-arm recall plus temporal retrieval. Test that idea only after defining a labeled retrieval problem over eligible/local evidence. Existing private archive and explicit confirmed edits remain source authority. Reflective/generated opinions cannot become user preferences automatically.

## AX boundary

The useful concept is workload fencing and resumable isolation, not Kubernetes itself. Current hourly/project work should remain on the existing repository/GitHub automation unless scale/isolation evidence proves a cluster is necessary.

## Runtime/dependency changes

None:
- no Paperclip server;
- no TypeSafe/Jev key or browser-harness;
- no Laya/Torch/Transformers/model checkpoint;
- no Kubernetes/AX/Agent Substrate;
- no Hindsight server/client/database;
- no new production state authority.

The concrete adoption is repository-owned governance/admission policy in `.agents/skills/verified-agent-orchestration/SKILL.md`.
