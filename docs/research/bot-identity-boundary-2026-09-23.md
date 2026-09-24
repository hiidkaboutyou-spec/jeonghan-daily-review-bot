# Bot identity boundary — 2026-09-23

## Outcome and evidence

Remove the remaining name-based bot identity decisions in the production workflows without changing dispatch cadence, permissions, state, X collection, Telegram delivery, or recovery triggers. The reviewed baseline is `main` commit `03ce591ba824b05a77b0db336d3aa6b8f9b0fd57` after PR #122.

The [exact-head zizmor run on PR #122](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/35858437848) still reports a high-confidence `bot-conditions` finding for `github.actor == 'github-actions[bot]'`. [zizmor's audit documentation](https://docs.zizmor.sh/audits/#bot-conditions) explains why a name-based `github.actor` check is a weak authorization boundary. [GitHub's context reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#github-context) documents `github.actor_id` as the account ID that triggered the initial run. The repository's real [Daily dispatch](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/35858628756) and [watchdog dispatch](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/35858880180) both record `github-actions[bot]` with account ID `41898282` in the GitHub Actions runs API. This observed ID is used for the built-in GitHub Actions account; it is not a repository secret.

## Change

- The privileged watchdog accepts an explicitly dispatched run only when `github.actor_id` matches the verified numeric bot account ID. Its `workflow_run` exclusion compares the upstream run actor's numeric `id`, keeping the one-watchdog-per-recovery rule.
- The Daily workflow re-arms the watchdog only for a live automated recovery with that same numeric account ID. Its nightly Fanfic already-covered query also compares the account ID, preserving the existing one-digest-per-day intent. The encrypted snapshot cadence still favors manual dispatches, now distinguished from automated dispatches by numeric account ID.
- The watchdog heartbeat checks the actor ID returned by GitHub's runs API when it classifies a failed automated recovery. The rest of the watchdog decision engine, `SOURCE_ACTOR` event/input metadata, token scope, concurrency, and production schedules are unchanged. The dispatch-only job gate is the privilege boundary; its source actor input is provided only after that gate and is not used to authorize the job.

## Validation and failure/rollback

Workflow regression tests assert the numeric checks in all three locations and forbid the old name-based checks. Run the repository's baseline and exact-head actionlint/zizmor CI. No live dispatch or Telegram call is part of PR validation. If GitHub ceases to provide the observed bot account ID, automated re-arm and bot-dispatched watchdog execution fail closed; the independent scheduled heartbeat remains in place. Revert this PR to restore the previous guards if production evidence contradicts the account-ID assumption. No schema or state migration is involved.

After merge, inspect a real `main` Daily recovery and Watchdog sequence before promoting zizmor from report-only; a passing linter alone cannot prove live recovery. Do not use the workflow security change as evidence that the existing X/Cloudflare 403 outage is fixed.

## External GitHub repository decision

The concrete gap was workflow identity, so the already pinned CI-only [zizmor](https://github.com/zizmorcore/zizmor) and [actionlint](https://github.com/rhysd/actionlint) are the right tools; adding another runtime or agent framework cannot close this gap. The [existing capability scan](github-capability-scan-2026-09-18.md) and [bounded Agent Reach recovery](../AGENT_REACH_X_RECOVERY.md) already cover the proposed X tooling and persistence patterns. Agent Reach is pinned in `requirements.txt` and has a constrained X fallback; installing its full system integration or a second X collector would add overlapping authority and browser/cookie access. No new dependency is installed in this stage. Revisit SQLite online-backup and translation evaluation candidates when those specific milestones are implemented, with dependency, license and privacy review at that time.
