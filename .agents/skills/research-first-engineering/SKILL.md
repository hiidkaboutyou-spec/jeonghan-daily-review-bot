---
name: research-first-engineering
description: Mandatory evidence-first workflow for bugs, features, regressions, integrations, and production-behavior changes. Investigate root cause, research proven patterns, prove RED before GREEN, and verify the exact production path before claiming success.
---

# Research-First Engineering

Use this skill for every bugfix, behavior change, integration, performance issue, production regression, or request equivalent to "make this work properly".

This skill adapts the strongest ideas from Superpowers systematic debugging, TDD, and verification-before-completion to this repository. Repository rules in `AGENTS.md` remain higher authority.

## Iron laws

1. **No fix before root-cause investigation.**
2. **No production behavior change without a regression test that proves the old behavior fails or is insufficient.**
3. **No "fixed / done / works" claim without fresh evidence for the exact claim.**
4. **Green unit tests do not prove the user-visible production path.**
5. **Do not trust an agent, previous run, remembered state, or CI summary when current repository/runtime evidence can be inspected.**

## Phase 0 — Recover current truth

Before changing code:

- read `AGENTS.md`, affected modules/tests/workflows/config, and task-relevant status/handoff docs;
- verify current `main` SHA and open PRs;
- inspect recent changes touching the same runtime path;
- inspect relevant CI/workflow evidence;
- identify the concrete production class/function actually executed, including overrides/mixins/monkey patches;
- distinguish canonical `main` from branch-only/pilot behavior.

If memory/docs disagree with GitHub, GitHub/current code wins and the mismatch must be recorded.

## Phase 1 — Reproduce and trace root cause

For a bug:

1. capture the exact user-visible symptom and exact input when available;
2. read full error/stack trace/log evidence;
3. reproduce on the closest deterministic path to production;
4. trace call/data flow backward until the failing boundary is identified;
5. inspect subclass overrides and wrappers, not only the base implementation;
6. compare with a nearby working path;
7. state one concrete hypothesis: "X is the root cause because Y evidence".

If the problem cannot be reproduced, add bounded diagnostics or obtain stronger runtime evidence. Do not guess.

If three distinct fixes fail, stop patching symptoms and reassess the architecture.

## Phase 2 — Research before design

When external behavior, architecture, dependency choice, rate limits, platform behavior, or a known integration pattern could materially affect the fix:

- search GitHub repositories, source, issues, PRs, discussions, releases;
- prefer official documentation/specifications and primary upstream source;
- use web/community sources only as secondary evidence;
- compare at least the plausible alternatives instead of copying the first result.

For any dependency/tool/service, record:

- exact gap solved;
- whether the repository already has the capability;
- adopt vs adapt-ideas-only vs reject/defer;
- maintenance/release activity;
- license;
- security/transitive risk;
- supported runtime/platform compatibility;
- network/rate-limit/privacy implications;
- rollback/removal path.

Popularity alone is not evidence.

## Phase 3 — RED before implementation

Create the smallest regression test or deterministic reproduction that fails for the observed reason.

Requirements:

- test the real behavior boundary, not only a mock call count;
- instantiate the concrete production class/path when inheritance or wiring matters;
- include the real input shape that triggered the bug when safe;
- verify the test fails before changing production code;
- failure must be for the expected reason, not a typo/setup error.

If a true automated RED is impossible, create the narrowest executable reproduction and document why.

## Phase 4 — Minimal root-cause fix

- change the source of the defect, not a downstream symptom;
- one hypothesis/fix at a time;
- do not bundle unrelated refactors;
- preserve persistence/idempotency/retry/privacy/security boundaries;
- adapt proven upstream ideas rather than importing heavy frameworks when local code is sufficient.

## Phase 5 — Verification ladder

Freshly verify, in order:

1. focused regression test;
2. related test group;
3. repository baseline tests/checks;
4. exact concrete production path;
5. integration boundary affected by the change;
6. PR exact-head CI;
7. after merge, real `main` execution when the change affects user-visible production behavior.

For every success claim, identify the evidence that proves it. If the final user-visible behavior has not been exercised, say **code fixed / CI green / production not yet observed** instead of "fixed".

## Completion vocabulary

Use these states precisely:

- **root cause identified** — evidence explains failure;
- **regression reproduced** — RED observed;
- **code fix implemented** — code changed, not yet fully verified;
- **PR-head verified** — exact-head required checks passed;
- **merged to main** — canonical code updated;
- **production-path verified** — real main execution exercised the corrected path;
- **user-visible confirmed** — observed user outcome is correct.

Never collapse these into one "done" statement.

## Review

Before merge, independently review the diff against:
- original symptom;
- root-cause hypothesis;
- regression test;
- invariants in `AGENTS.md`;
- unintended behavior changes;
- dependency/security/privacy impact;
- rollback.

Critical or important findings block merge.
