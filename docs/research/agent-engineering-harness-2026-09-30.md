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
