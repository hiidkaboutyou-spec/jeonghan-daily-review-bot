# Agent engineering harness research — 2026-09-30

## Goal

Reduce repeated agent failure modes where a plausible change passes narrow tests but misses the concrete production path.

The desired behavior is:
- research material uncertainty before implementation;
- find root cause before patching;
- reproduce bugs before repair where feasible;
- add regression protection that exercises the real failing path;
- validate progressively;
- distinguish PR CI from production acceptance;
- never claim success beyond the available evidence.

## Reviewed upstreams

### OpenAI Plugins / Superpowers

The old `openai/skills` catalog is now deprecated and points users to `openai/plugins` for current Codex plugin examples.

The official OpenAI Plugins repository packages Superpowers as a Codex plugin. Reviewed manifest:
- plugin: `plugins/superpowers`
- version observed: `6.3.0`
- upstream project: `obra/superpowers`
- license: MIT

Useful upstream ideas:
- systematic root-cause debugging;
- regression-first/TDD behavior;
- evidence before completion claims;
- code review before merge;
- plan/execute/converge discipline.

Decision: **adapt the process, do not copy the whole plugin into this runtime repository**.

Reasons:
- the project already has strong repository-specific `AGENTS.md` and next-stage skills;
- full generic framework installation is a user/agent-environment concern, not a production dependency;
- project-specific production acceptance (Telegram concrete class, GitHub Actions, durable offsets/state) is stricter than generic debugging guidance.

### AGENTS.md

Reviewed `agentsmd/agents.md` (MIT). It defines AGENTS.md as a predictable repository-local instruction surface for coding agents.

Decision: keep project policy in root `AGENTS.md`, with task workflows in `.agents/skills/`.

### GitHub Spec Kit

The repository already has an approved optional Spec Kit policy. Current upstream now explicitly exposes separate processes for specification, bug fixing, and idea assessment.

Decision: retain it as optional developer-side planning/convergence tooling. Do not make it a runtime or mandatory install dependency.

## Architecture decision

The repository gets a two-level evidence harness:

1. `.agents/skills/evidence-first-engineering/SKILL.md`
   - universal research/debug/verification contract.

2. `.agents/skills/hani-production-acceptance/SKILL.md`
   - project-specific proof requirements for Telegram, shared X links, collectors, translation, media, state and Actions.

`AGENTS.md` activates them for substantive work and forbids broad completion claims without matching evidence.

## Why this would have caught the 2026-09-30 Telegram regression

The earlier long-poll change tested the base `Application` path. Production used an override in `TelegramSafeReviewApplication` through the concrete `WebhookAwarePersonalAssistant`. The new contract explicitly requires checking concrete runtime composition and exercising production overrides before claiming an owner-visible runtime fix.

## External plugin policy

Superpowers can be useful in the agent environment, but it is optional. Repository-owned skills remain authoritative for this project and must work even when the external plugin is absent.

No package, service, secret, runtime dependency, or production network call is added by this harness.


## Implementation record

### Canonical result

The evidence-first engineering harness was implemented in PR #148 and squash-merged to `main`.

- PR: `#148 chore: add evidence-first engineering harness`
- merged commit: `71628cbeccd5281917286b74c30298dbc0ba5e00`
- canonical branch after merge: `main`
- runtime impact: none; this change is developer/agent process infrastructure only.

### Files added or changed

- `.agents/skills/evidence-first-engineering/SKILL.md`
  - requires repository/GitHub preflight before mutation;
  - separates symptom, root-cause hypothesis and acceptance evidence;
  - requires reproduction/equivalent evidence before a bug fix;
  - requires primary/upstream research when platform/API/dependency behavior is material;
  - requires regression-first implementation;
  - uses a verification ladder from focused regression to post-merge evidence;
  - forbids claiming more than the strongest proven evidence level.

- `.agents/skills/hani-production-acceptance/SKILL.md`
  - requires the concrete production application path for Telegram behavior;
  - requires real shared-X-link flow validation;
  - requires cursor/completeness evidence for X collection;
  - requires production translation smoke plus separate human/editorial quality evidence;
  - requires media delivery/idempotency checks when media changes;
  - requires state/recovery/restart checks when persistence changes;
  - requires a real post-merge `main` run before production verification for runtime/workflow changes.

- `AGENTS.md`
  - now makes the evidence-first contract mandatory for substantive bugs, features, refactors, dependency/tool choices, performance/reliability work, and any task that may end with a completion claim;
  - explicitly distinguishes: implemented -> PR-head verified -> merged -> post-merge main verified -> owner-visible confirmed.

- `tools/validate_agent_harness.py`
  - validates required skill presence;
  - validates skill frontmatter names/descriptions;
  - validates that `AGENTS.md` activates the required skills;
  - validates that the mandatory evidence-first contract remains present.

- `.github/workflows/agent-harness.yml`
  - runs only for agent-contract/tooling changes;
  - validates the repository-owned harness without touching production state or live Telegram delivery.

- this research note
  - records upstream research, adoption/rejection decisions and durable handoff.

### External research and adoption decisions

Reviewed:
- current `openai/plugins` packaging for Codex plugins;
- Superpowers `6.3.0`, upstream `obra/superpowers`, MIT;
- `agentsmd/agents.md`, MIT;
- GitHub Spec Kit;
- the deprecated `openai/skills` catalog, which now points to the Plugins repository.

Decision:
- adapt the useful Superpowers methods into repository-owned rules;
- do not vendor the whole framework into the production repository;
- do not add any runtime package/service/secret;
- keep Superpowers optional in the external agent environment;
- keep repository-owned skills authoritative even when Superpowers is absent.

### Why this change was needed

The 2026-09-30 Telegram long-poll regression demonstrated the gap:

- a base-class path was tested;
- production used an override in the concrete runtime composition;
- the narrow tests therefore did not prove the production path.

The new acceptance contract explicitly requires checking overrides/wrappers/subclasses/entrypoints and testing the same concrete boundary that failed.

### Verification evidence

On the final PR head `ea8c26347693ba05e0329a025e8c9df89a6145f8`, all 10 observed workflows completed successfully:

- Agent Harness Contract;
- Workflow Security Lint;
- Hani Workflow Safety;
- Nightly Jeonghan Fanfic Digest;
- Render Production Validation;
- Hani CodeQL;
- Jeonghan Daily Review Bot;
- Hani Maintenance Diagnostics;
- Channel Style Translation Benchmark;
- Hani Security Diagnostics.

After merge, the dedicated Agent Harness Contract also completed successfully on `main@71628cbeccd5281917286b74c30298dbc0ba5e00` as run `36701931950`.

Because this PR changes only developer/agent instructions and validation, not bot runtime behavior, no owner-visible Telegram acceptance step is introduced by this PR itself.

### External plugin state

Superpowers was surfaced as an optional ChatGPT/Codex plugin candidate. It was **not** silently installed or treated as a project dependency. Any installation/connection remains a separate user-authorized environment action.

### Ongoing rule

Future work on this repository should leave a durable record of:
- root cause or product goal;
- research used;
- adopt/adapt/reject decisions;
- exact changed surfaces;
- regression/acceptance coverage;
- CI actually run;
- what is and is not proven;
- rollback/migration notes;
- exact next frontier.

The repository/GitHub state remains implementation authority; chat memory is not sufficient proof.
