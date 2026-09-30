# Evidence-first agent harness research — 2026-09-30

## Why this exists

A recent production regression showed a process gap: a base-class Telegram long-poll implementation had green tests, but the concrete production class overrode the method with an incompatible signature. The change looked verified before the exact production path had been proven.

The fix is not "more tools". It is a repository-owned contract that requires root-cause investigation, external research when material, RED/GREEN regression proof, concrete production-path validation, and evidence-calibrated completion claims.

## Upstreams reviewed

### Superpowers

Reviewed through the current official Codex plugin distribution in `openai/plugins` (Superpowers 6.3.0, MIT). The upstream repository is `obra/superpowers`.

Adopted ideas:
- systematic debugging: root cause before fixes;
- compare working and broken paths;
- one hypothesis at a time;
- failing regression before implementation;
- fresh verification before completion claims;
- explicit code review before integration.

Not copied wholesale:
- worktree/subagent orchestration is harness-specific;
- repository safety rules remain authoritative;
- no coding plugin may become a production runtime dependency.

### AGENTS.md

`agentsmd/agents.md` is an open agent-guidance convention (MIT). This repository already uses `AGENTS.md`, so permanent engineering rules belong there rather than only in chat memory.

### GitHub Spec Kit

Already approved here as optional developer-side process tooling for substantial planned stages. It complements, but does not replace, bug reproduction and production acceptance.

### OpenAI skills / plugins

The old `openai/skills` repository currently marks itself deprecated and directs users to the OpenAI Plugins repository for current Codex plugin/skill examples. Therefore the current upstream reference is `openai/plugins`, not the deprecated catalog.

## Decision

Use a layered model:

1. Superpowers in the coding-agent harness for reusable debugging/TDD/verification behavior.
2. Repository-owned `AGENTS.md` for permanent invariants.
3. `.agents/skills/research-first-engineering` for every bug, integration, performance issue, or behavior change.
4. `.agents/skills/hani-production-acceptance` for owner-visible Telegram/X/translation/state/runtime changes.
5. Existing `project-next-stage` + Spec Kit process for broad roadmap work.

## Required acceptance consequence

A future defect equivalent to the Telegram production long-poll crash must be caught before any "fixed" claim because the workflow now requires:
- inspection of the concrete production override;
- a regression against the actual production class/path;
- exact-head CI;
- a real post-merge `main` run for owner-visible runtime changes;
- explicit separation of "code fixed", "CI green", "production-path verified", and "owner-visible confirmed".
