# Anthropic public agent patterns — 2026-10-05

## Scope

This note records which Claude/Anthropic-related GitHub material was considered for the Jeonghan Daily Review Bot and what was actually adopted.

The goal is not to import Claude internals. The goal is to borrow safe engineering patterns that improve scheduled agent work on this repository.

## Sources reviewed

### Adopt/adapt

1. `anthropics/claude-agent-sdk-python`
   - license: MIT;
   - useful public patterns: resumable session stores, session summaries, hooks around tool use, and explicit tool-permission callbacks;
   - decision: **adapt concepts only** into repository-owned agent guidance. No SDK/runtime dependency added.

2. `anthropics/claude-plugins-official`
   - relevant components reviewed: automation recommender, hook-pattern references, Hookify, and CLAUDE.md-management tooling;
   - relevant component licenses observed: Apache-2.0;
   - decision: **adapt concepts only**. Keep this repository's `AGENTS.md` and skills authoritative.

### Rejected as implementation sources

- repositories advertising leaked/system-prompt dumps;
- reverse-engineered/decompiled Claude Code mirrors;
- repositories with unclear or missing licensing for the material we would need to copy.

These can reveal that a pattern exists, but they are not a code/prompt source for this project.

## Gap identified in this repository

The existing evidence-first harness is strong about debugging, testing, and completion claims, but scheduled/resumed runs still need an explicit contract for:

- reconstructing the active working set after a session boundary;
- detecting stale branch/PR/main assumptions before continuing;
- classifying writes versus live/destructive side effects before execution;
- checking the actual returned result after each mutation;
- preventing blind retry loops and duplicate side effects;
- leaving a compact durable handoff for the next run.

## Implemented adaptation

Added `.agents/skills/agent-session-safety/SKILL.md` and activated it from `AGENTS.md` plus `project-next-stage`.

The skill deliberately keeps the implementation repository-native:

- no Anthropic package or model dependency;
- no new secret;
- no new production network path;
- no Telegram/runtime behavior change;
- no copied leaked/decompiled prompt content.

The agent-harness validator now requires the skill so future edits cannot silently drop the contract.

## Expected benefit for Hani scheduled work

Hourly/deep agent runs should now be less likely to:

- resume from stale GitHub state;
- write to the wrong branch/path;
- repeat a failed mutation indefinitely;
- replay an external side effect after an uncertain result;
- forget which evidence is actually current;
- claim a prior session's result without revalidation.

This is process safety for engineering automation, not evidence that any Telegram/X/translation production defect is fixed.
