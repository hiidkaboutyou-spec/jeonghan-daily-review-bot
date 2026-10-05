---
name: agent-session-safety
description: Preserve reliable session continuity and gate repository mutations during long-running, scheduled, or tool-heavy agent work.
---

# Agent Session Safety

Use this skill for broad next-stage work, scheduled/hourly engineering runs, multi-step investigations, or any task that may span multiple tool calls, commits, workflows, or resumed sessions.

This is repository engineering guidance. It must not become a bot runtime dependency.

## 1. Establish a resumable working set

Before mutation, record the minimum current working set:

- user-visible goal;
- canonical `main` SHA;
- active branch/PR and prerequisite PRs;
- strongest verified evidence so far;
- task-relevant files/workflows;
- unresolved blocker or next action.

Treat previous chat/session summaries as hints only. Revalidate repository truth before acting.

## 2. Resume safely

When continuing earlier work:

1. compare the saved branch/PR/main references with current GitHub state;
2. discard stale assumptions when the repository moved;
3. re-check task-relevant CI and open PRs;
4. continue from the first unproven step, not from the last claimed step;
5. never replay a write merely because a previous session is uncertain.

A resumed session must be idempotent with respect to repository writes and production-side effects.

## 3. Keep context bounded

Preserve high-signal facts, not raw tool transcripts.

Prefer:
- exact SHAs/PR numbers;
- root cause and acceptance criteria;
- changed paths;
- test/workflow conclusions;
- explicit blockers and rollback.

Drop stale hypotheses, duplicated logs, and large unrelated outputs. Never compact away a failing check, unresolved review comment, production-risk caveat, or required acceptance step.

## 4. Pre-action mutation gate

Before every write or external side effect, classify the action:

- **read-only**: inspect/search/fetch;
- **repo-local write**: branch/file/commit/PR changes;
- **external side effect**: live Telegram delivery, provider call with private data, deployment, release, secret/config mutation;
- **destructive**: delete/overwrite/force operation or irreversible production action.

For repo-local writes:
- confirm the target repo/branch/path;
- confirm the write belongs to the current task;
- prefer a reviewable branch/PR;
- preserve state/schema/backward-compatibility rules.

For external/destructive actions:
- require explicit task authorization plus the repository's production-acceptance rules;
- never infer approval from an earlier unrelated run.

Never expose credentials, private chat identifiers, cookies, encrypted state, or private review data.

## 5. Post-action verification hook

After every mutation:

1. read the returned status/commit/PR/workflow result;
2. verify the intended target actually changed;
3. record the exact evidence level achieved;
4. if the action failed, diagnose before retrying;
5. do not repeat the same failing write in a loop.

After three materially different failed fix attempts, review the architecture/coupling before another patch.

## 6. Handoff checkpoint

Before ending a substantial run, leave a durable handoff in the existing project/repository memory surface that includes:

- current `main` and active PR/branch;
- what changed;
- what was actually validated;
- what remains unproven;
- blockers/risks;
- exact next action.

Do not store secrets, private Telegram content, decrypted state, or personal data in the checkpoint.

## 7. Provenance boundary

The ideas here are adapted from public Anthropic material with explicit open-source licensing, especially:

- `anthropics/claude-agent-sdk-python`: session stores/resume, hooks, and tool-permission callbacks (MIT);
- `anthropics/claude-plugins-official`: automation/hook patterns and repository-instruction management (Apache-2.0 components).

Do not vendor or copy from leaked, decompiled, reverse-engineered, or unclear-license Claude prompt/code dumps. Those may be used only as discovery leads; implementation must come from independently designed repository rules or clearly licensed public sources.
