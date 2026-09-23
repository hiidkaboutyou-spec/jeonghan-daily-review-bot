# Deep Next-Stage Execution Protocol

## Purpose

This protocol defines what a request such as **"do the next stage"**, **"continue the project deeply"**, or equivalent means for the Jeonghan Daily Review Bot.

The target is **maximum safe coherent progress toward the production assistant goal** while protecting production stability, recoverable state, deduplication/idempotency, private review data, and bounded automation.

This protocol is developer/agent tooling only. It must not become a production runtime dependency.

## Core rule

Choose the **largest coherent stage that can be researched, implemented, validated, and converged safely** from the verified production frontier.

Do not:
- pick a stage from remembered numbering;
- skip unfinished prerequisite PRs or pilots;
- make a broad rewrite when an incremental vertical slice can close the same gap;
- treat documentation-only movement as stage completion when safe implementation is reachable;
- mix unrelated collectors, UI, persistence, and infrastructure work only to create a large diff.

When necessary, use an explicit PR stack so a large stage remains reviewable.

## 1. Recover authoritative context

Before substantive work:

1. Read `AGENTS.md`, `README.md`, launch/status/roadmap/audit docs, affected modules/tests/workflows/config.
2. Load Project Memory/PMC/projectmem when available and permitted.
3. Verify GitHub reality:
   - `main` HEAD;
   - open PRs and their bases;
   - pilot/shadow branches;
   - workflow/check evidence;
   - recent production-relevant commits.
4. GitHub/current code is implementation authority; memory is context.
5. Flag stale memory instead of silently trusting or rewriting it.

## 2. Determine the production frontier

Map:

- canonical production behavior;
- non-canonical pilot/shadow/PR behavior;
- incomplete exit criteria;
- reliability or source-health blockers;
- next reachable product outcome.

Prefer completing or hardening a currently active stage/pilot before starting an unrelated later capability.

## 3. Deep research

Use primary sources whenever a decision depends on external behavior:

- upstream source/release notes;
- official platform/API documentation;
- exact licensing;
- security advisories;
- GitHub Actions/runtime constraints;
- measured repository-local production evidence.

For every proposed dependency/service/repository, assess:
1. concrete gap;
2. overlap with current code;
3. adopt vs adapt idea vs reject/defer;
4. maintenance and release activity;
5. license;
6. security/transitive risk;
7. Python 3.11/macOS/Linux/GitHub-hosted-runner compatibility as relevant;
8. network/rate-limit/availability behavior;
9. secret/private-data exposure;
10. failure, rollback, and removal path.

Popularity is not sufficient evidence.

## 4. Scope: maximum safe coherent progress

A stage should normally deliver an end-to-end capability or close a production/reliability milestone, including:

- implementation;
- persistence/migration compatibility where needed;
- idempotency/dedup behavior;
- failure/retry handling;
- tests;
- workflow/config changes;
- operational documentation;
- rollback/recovery evidence.

Select a smaller stage only when a broader one would cross an unresolved production, authorization, security, or data-integrity boundary.

## 5. Define success before implementation

Record:
- user/production outcome;
- current evidence;
- non-goals;
- architecture boundaries;
- state compatibility/migration rules;
- secret/privacy constraints;
- failure modes;
- validation matrix;
- exit criteria.

When Spec Kit is available, substantial work should use:

`specify -> clarify -> plan -> checklist -> tasks -> analyze -> implement -> converge`

Repeat implementation/convergence until material gaps are closed.

When unavailable, perform the equivalent process with repository-native artifacts. Spec Kit must not be required for the bot to run.

## 6. Implementation safety

- Preserve incremental architecture.
- Do not weaken filtering, dedup, state recovery, watchdog, or delivery safeguards.
- Keep network calls bounded.
- Preserve persistence compatibility or ship a tested migration and rollback.
- Never let validation-only work send live Telegram messages or mutate production state.
- Treat GitHub Actions/cache/encrypted backup/private SQLite as production infrastructure.
- Prefer shadow/dry-run/evidence modes before activation when behavior can affect live delivery.
- Couple a new capability with the regression tests that prove retries/restarts/overlaps remain safe.

## 7. Privacy and secret gates

Never commit/log/expose:
- Telegram tokens or private chat identifiers;
- X cookies/auth material;
- decrypted recovery state;
- private review/archive data;
- user-private content.

A new external service must not receive private review/state data merely for convenience.

## 8. Validation

Run the repository baseline from `AGENTS.md`:

```bash
python -m pip install -r requirements.txt
python -m pip check
python -m compileall -q app tests tools
python -m app --check
python -m unittest discover -s tests -p "test_*.py" -v
```

Add task-specific gates for edited GitHub Actions YAML, state/migrations, optional media, watchdog, source behavior, crash/restart, rate limits, or delivery idempotency.

If the environment cannot execute a required gate:
- never claim it passed;
- inspect CI evidence when available;
- leave the PR unmerged when the missing evidence is safety-critical.

## 9. Convergence

Compare current implementation with the stage's outcome and classify gaps:

- missing;
- partial;
- contradictory;
- unrequested/risky.

Fix material gaps, rerun tests, and repeat until the stage is genuinely closed or an explicit blocker remains.

## 10. Durable handoff

At stage completion:

1. update the authoritative launch/status/roadmap/audit docs that actually changed;
2. record important external-tool research and adopt/reject decisions;
3. update Project Memory/PMC/projectmem when available;
4. keep secrets/private runtime data out of engineering memory;
5. ensure the PR records capability, safety boundaries, validation, migration/rollback, and next verified frontier.

Do not maintain a redundant mutable current-state document when an existing canonical status file already owns that information.

## 11. PR/merge policy

- Keep large work reviewable via one focused PR or an explicit stack.
- Do not force-push shared branches.
- Do not bypass CI.
- Do not merge a pilot/activation while required safety evidence is missing.
- Preserve explicit dependency on non-canonical prerequisite PRs.

## Spec Kit integration policy

GitHub Spec Kit is approved as an **optional developer-side process harness** only.

Reviewed baseline at adoption:
- upstream: https://github.com/github/spec-kit
- upstream license: MIT
- pinned reviewed release: `v1.0.8`
- existing-project guide: https://github.com/github/spec-kit/blob/main/docs/guides/existing-projects.md
- Codex integration uses `.agents/skills`;
- full substantial-work flow ends with convergence.

Rules:
- initialize only on a reviewable branch;
- inspect generated diffs;
- never run forced initialization over unreviewed dirty work;
- never add `specify` to production requirements;
- repository `AGENTS.md` and production-safety rules override generic templates;
- upgrades are deliberate and reviewed.

## Definition of a good next stage

After the run, the bot should have a meaningful new/closed production capability or reliability milestone, backed by tests and operational evidence, without silently increasing duplicate-delivery, state-loss, secret-exposure, rate-limit, or live-automation risk.
