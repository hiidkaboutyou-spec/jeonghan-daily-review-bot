---
name: project-next-stage
description: Execute the next substantial safe stage of the Jeonghan Daily Review Bot with deep research, implementation, production-safety validation, convergence, and durable handoff.
---

# Project Next Stage

Use this skill when the user asks to "do the next stage", "continue deeply", "advance the project", or equivalent without prescribing a narrow change.

Read `AGENTS.md` and `docs/AGENT_NEXT_STAGE_PROTOCOL.md` first.

## Required behavior

1. Reconstruct repository and production truth before choosing work.
2. Load available project memory, then verify it against `main`, open PRs/pilots, workflows, roadmap/audit docs, and current code.
3. Choose the **largest safe coherent slice** at the real frontier.
4. Research external tools/platform behavior deeply when it affects architecture, collection reliability, security, licensing, privacy, rate limits, or operations.
5. Define success, failure modes, rollback, and validation before implementation.
6. Implement the full vertical slice, including persistence/migration/idempotency/retry/tests/docs that are needed for production trust.
7. Keep validation paths non-live unless activation itself is explicitly the reviewed stage.
8. Run required tests and CI evidence; never call an unexecuted gate passing.
9. Converge against the intended outcome and repair material gaps.
10. Update durable project/repository memory without storing secrets or private runtime data.
11. Use a reviewable PR or explicit PR stack and preserve prerequisite dependencies.

## Autonomy

Resolve routine engineering choices from evidence without asking the user. Stop only for a real authorization/secret, irreducible product decision, or unsafe boundary that cannot be inferred.

## Size target

Prefer a substantial end-to-end capability or reliability milestone over isolated helpers. If an active pilot/PR is the frontier, finish or harden it instead of jumping ahead.

## Tools

Use GitHub, current web research, Project Memory/PMC/projectmem, and other connected plugins when they materially improve the stage. Do not use tools merely because they are available.

## Final handoff

Report:
- verified production frontier;
- substantial capability completed;
- external research decisions;
- changed surfaces;
- tests/CI actually run;
- activation/migration/rollback status;
- blockers/non-canonical dependencies;
- exact next frontier.
