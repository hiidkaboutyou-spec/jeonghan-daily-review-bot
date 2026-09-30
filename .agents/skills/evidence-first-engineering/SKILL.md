---
name: evidence-first-engineering
description: Mandatory evidence-first workflow for bugs, features, refactors, dependency changes, and any claim that work is fixed, complete, safe, or production-ready.
---

# Evidence-First Engineering

Use this skill for any substantive code change, and always for:
- production bugs or unexpected behavior;
- failed CI/tests;
- performance/reliability regressions;
- dependency/tool adoption;
- changes that cross runtime boundaries;
- any task where the final answer would say "fixed", "done", "works", "safe", or "production-ready".

Repository `AGENTS.md` and project-specific skills remain higher authority.

## 1. Preflight before mutation

Recover current truth before changing code:

1. read `AGENTS.md` and task-relevant status/handoff/roadmap docs;
2. inspect canonical `main` HEAD;
3. inspect open PRs/branches that overlap the task;
4. inspect recent task-relevant commits;
5. inspect the real workflow/runtime path, not only the obvious base class/helper;
6. identify existing tests and CI gates for that path.

Do not edit first and investigate afterward.

## 2. Define the observable outcome

Write down the behavior that must become true from the user's or production system's point of view.

Separate:
- **symptom** — what is visible;
- **root-cause hypothesis** — what may explain it;
- **acceptance evidence** — what would prove the outcome is actually fixed.

A green unit test is not automatically acceptance evidence.

## 3. Reproduce or gather equivalent evidence

For a bug, reproduce it before fixing whenever feasible.

Preferred order:
1. exact local reproduction;
2. regression test that fails for the same reason;
3. production/CI log or trace proving the failure path;
4. deterministic minimal harness matching the concrete production class/path.

If reproduction is impossible, state why and gather the strongest available evidence. Do not invent a cause.

## 4. Research before designing when uncertainty is material

Use external research when behavior depends on an API/platform/framework/dependency or when the failure pattern is not well understood.

Prefer:
- official documentation/specifications;
- upstream source, issues, PRs, releases and changelogs;
- security advisories and exact license;
- real implementations in maintained repositories;
- community reports only as supporting evidence.

For any proposed external repository/tool, record:
- exact gap it solves;
- adopt vs adapt idea vs reject/defer;
- maintenance/activity;
- license;
- compatibility with this repo/runtime;
- security/privacy/network impact;
- rollback/removal path.

Popularity alone is not adoption evidence.

## 5. Confirm one root cause before the real fix

Do not stack speculative fixes.

Use a single explicit hypothesis:
- "I think X causes Y because evidence Z."

Test the hypothesis with the smallest safe experiment. If it fails, return to investigation.

After three materially different failed fix attempts, stop treating the issue as a local bug and review the architecture/coupling before attempting another patch.

## 6. Regression-first implementation

For a bug:
1. add the smallest regression test/harness that fails for the demonstrated reason;
2. confirm the failure is meaningful, not a typo/setup failure;
3. implement the smallest coherent root-cause fix;
4. rerun the focused regression.

For a feature:
1. define acceptance tests/contracts first;
2. implement against them;
3. test failure modes, not only the happy path.

Mocks may isolate external systems, but at least one test must exercise the concrete production integration boundary when the bug lives there.

## 7. Verification ladder

Run evidence in increasing scope:

1. focused regression;
2. related module/integration tests;
3. repository baseline from `AGENTS.md`;
4. task-specific CI/workflow gates;
5. concrete production-path acceptance when the change affects production behavior;
6. post-merge `main` evidence when merge/runtime behavior can differ from PR CI.

Read the result, exit/conclusion, and failing step. Do not infer success from "workflow started" or from another workflow with a similar name.

## 8. Independent convergence review

Before merge, compare the current diff against:
- original user-visible outcome;
- root cause;
- regression reproduction;
- architecture/privacy/state constraints;
- rollback;
- all required acceptance evidence.

Ask:
- Did we test the same concrete path that failed?
- Could an override/subclass/adapter bypass the tested base path?
- Could CI differ from production due to env, state, scheduling or credentials?
- Did we accidentally add a second writer/consumer/state authority?
- Is there a stale test that only proves an implementation detail?

Fix material gaps before merge.

## 9. Completion-claim gate

Never claim "fixed", "complete", "safe", "working", or equivalent unless fresh evidence supports that exact claim.

A final status must distinguish:
- **implemented**;
- **PR-head verified**;
- **merged to main**;
- **post-merge production-path verified**;
- **owner-visible behavior verified**.

Do not collapse these into one status.

If the last required acceptance step needs a human-visible action, say exactly that instead of claiming success.

## 10. Handoff

Record:
- root cause;
- evidence used;
- external research decision;
- files/surfaces changed;
- regression added;
- tests/CI actually run;
- what is and is not proven;
- rollback;
- remaining acceptance step or exact next frontier.
