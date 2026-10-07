# Hani Engineering Worklog — 2026-09-25 to 2026-09-30

This file is the durable handoff for the Jeonghan Daily Review Bot work completed during the current engineering cycle. It records what was verified, implemented, rejected, blocked, and what must happen next. Chat summaries are not the source of truth; GitHub state and this worklog are.

## Current canonical state

- Canonical branch: `main`
- Canonical main SHA at this handoff: `71628cbeccd5281917286b74c30298dbc0ba5e00`
- Latest canonical change: PR #148, `chore: add evidence-first engineering harness`
- Active refresh branch for checkpoint work: `fix/checkpoint-attestation-refresh-136`
- Refresh branch base at creation: exactly `main@71628cbeccd5281917286b74c30298dbc0ba5e00`
- Older checkpoint branch: `fix/database-checkpoint-attestation-136`
- Older checkpoint PR: #141 (Draft). Do not merge it as-is; its base is stale.
- Primary active blockers:
  - #140 — restore complete authenticated X retrieval with due-window proof
  - #136 — attest database checkpoint after Daily workflow step in production outcome
  - #113 — Stage C closure / remaining product frontier

## Why the checkpoint branch was refreshed

PR #141 was created from an older main (`26f84d96474019e75297ce95780d1a7ca5a90761`) and later became stale after newer product/engineering work landed, including Hani Inbox/editorial changes and the evidence-first engineering harness.

At the last live comparison before this handoff, #141 was 4 commits ahead but materially behind current main. Continuing to stack changes onto the stale branch would risk dropping newer behavior. A clean refresh branch was therefore created directly from the latest main instead of force-updating or force-merging the old PR.

## #136 — database checkpoint attestation

### Original production gap

`production-outcome.json` is emitted before the workflow performs the SQLite checkpoint. The workflow then performs:

- `PRAGMA quick_check`
- `PRAGMA wal_checkpoint(TRUNCATE)`

and uploads the already-created outcome artifact afterwards.

The old field `database_checkpoint_success=false` therefore could not distinguish:

1. checkpoint never attempted yet, from
2. checkpoint attempted and failed.

Consumers must not interpret the old false value as proof of checkpoint failure.

### Design decisions

The selected low-risk contract is:

- add `database_checkpoint_attempted` separately from success;
- legacy artifacts missing the field parse as `attempted=false`;
- `mark_database_checkpoint(...)` marks attempted before setting success;
- `attempted=true && success=false` classifies the outcome as `FAILED / database_checkpoint_failed`;
- persist attempted/failed state before opening SQLite so a mid-checkpoint crash cannot leave a false success;
- open the existing DB in read/write mode without silently creating a new DB on a bad path;
- require `quick_check == ok`;
- run `wal_checkpoint(TRUNCATE)`;
- treat `busy != 0` or an incomplete/partial checkpoint as failure;
- close the SQLite connection in all paths;
- only mark success after all proof conditions pass;
- no new runtime dependency is required.

### Work already implemented on the older branch

The older branch `fix/database-checkpoint-attestation-136` contains real runtime work, including:

- commit `8b56da8b76768ef0a8f685387a9f73df89863eba`
  - introduced `database_checkpoint_attempted` semantics in `app/production_outcome.py`;
- commit `80582b7ee234a44bd752846e93ca00723e884c94`
  - added a stdlib-only checkpoint attestation helper;
- commit `fbf6c63f938342d4a248b08122ac1c4232038b36`
  - added checkpoint regression coverage.

PR #141 was opened as a Draft to track this work.

### CI evidence on PR #141

Exact-head CI exposed a test-harness regression:

- several workflows failed because the new checkpoint tests imported `pytest`;
- the repository's validation path uses `python -m unittest discover ...`;
- `pytest` is not a project dependency;
- failure observed: `ModuleNotFoundError: No module named 'pytest'`.

The correct fix is to keep the tests stdlib-only:

- `unittest.TestCase`
- `tempfile.TemporaryDirectory`
- `subTest`
- `assertRaisesRegex`

Do **not** add pytest merely to satisfy the new tests.

### Remaining #136 work on the refreshed branch

Reapply the proven changes onto `fix/checkpoint-attestation-refresh-136` against current main:

1. production-outcome contract change;
2. checkpoint helper;
3. regression tests rewritten to pure `unittest`;
4. replace the inline checkpoint block in `.github/workflows/main.yml` with the helper;
5. open a fresh Draft PR or otherwise supersede stale #141 cleanly;
6. run exact-head CI on the refreshed branch;
7. merge only if all required workflows are green;
8. validate a real post-merge Daily run and independent Watchdog;
9. inspect the production artifact for:
   - `database_checkpoint_attempted=true`
   - `database_checkpoint_success=true`
10. close #136 only after production proof.

No force-push or force-merge should be used to salvage stale #141.

## #140 — X/Twitter retrieval and source completeness

### Production truth established

Authenticated X retrieval is the production blocker, not merely a CI cosmetic issue.

Evidence gathered across the cycle:

- authenticated `UserByScreenName` repeatedly returned HTTP 403;
- an isolated matrix tested Ubuntu, macOS, and Windows hosted runners;
- all three reproduced authenticated 403;
- changing hosted-runner OS therefore did not solve the problem;
- upgrading or swapping a scraper without proof is not considered recovery.

The system's fail-closed behavior has worked correctly:

- partial public fallback can retrieve many real updates;
- runs have recovered 27/31, 30/31, and in at least one case data from 31/31 configured sources;
- despite useful fallback data, source completeness remained unproven;
- outcome stayed `RECOVERY_REQUIRED`;
- `complete_source_count` remained 0 when boundary/termination proof was absent;
- global cursor was held as `partial_window`;
- Telegram delivery could still succeed for recovered content.

This distinction is intentional: "data returned" is not the same as "requested source window proven complete."

### Why completeness was not relaxed

The completeness engine requires provider evidence such as:

- valid timeline ordering;
- crossing the requested lower time boundary; or
- valid terminal/Bottom evidence.

Public syndication fallback can return useful posts but does not currently provide sufficient page-boundary/termination proof. Lowering the rule simply to make 30/31 or 31/31 appear green would corrupt cursor integrity and was rejected.

### Upstream / external research performed

GitHub was used as the primary source. Representative queries used during this cycle included:

- `twscrape 403 UserByScreenName 2026`
- `twscrape transaction id 2026 X bundle`
- `twitter scraper durable cursor checkpoint pagination sqlite retry`
- `twitter scraper pagination cursor resume sqlite 403 github actions 2026`
- `twitter syndication timeline cursor pagination scraper 2026`
- `Twitter X scraper cursor pagination resume rate limit 403 GitHub Actions 2026`
- `SQLite wal_checkpoint TRUNCATE busy complete automation Python 2026`

External projects/patterns reviewed included:

- `vladkens/twscrape`
  - useful baseline but upstream X bundle / transaction-ID changes and 403 behavior make it insufficient as a guaranteed fix;
  - search result completeness/order concerns mean search output cannot be used as authoritative timeline proof.
- `lhl/tweetxvault`
  - useful design reference for page-atomic persistence, bounded retry/cooldown, fail-fast 401/403, and not advancing a durable checkpoint for an incomplete page;
  - cookie/session based, so not adopted as production transport.
- `birdclaw`
  - useful patterns for resumable pagination, bounded pages, local SQLite/idempotent sync;
  - not a direct solution for hosted-runner authenticated 403.
- `xarchive`
  - useful explicit-coverage, pause/resume, backoff semantics;
  - browser/session-bound, so not selected as the production authority.
- `x-tweet-fetcher`
  - useful SQLite ledger / `tweet_id` primary-key / idempotent persistence ideas;
  - fallback transports do not prove full authoritative source coverage.
- `xKit` and similar cookie/undocumented GraphQL tools
  - rejected for production due to account/session risk, endpoint/query-ID fragility, challenge/rate-limit risk.
- browser/CDP/TLS-impersonation/proxy-rotation approaches
  - deliberately rejected as high account/policy/maintenance risk and not evidence-backed recovery.
- official X API
  - clean pagination/rate-limit semantics;
  - requires new credential/authorization and some capabilities may require paid access;
  - remains a candidate only through an isolated, authorized prototype.
- Xpoz and other third-party APIs
  - researched, but free-tier / tracked-item / credit constraints do not transparently satisfy 31-source production requirements;
  - requires new external credential/provider trust and must be evaluated in isolation before adoption.

### #140 architecture decision

Keep retrieval transport separate from integrity.

The low-risk integrity layer should support:

- per-source durable cursor;
- per-page persistence before cursor advancement;
- idempotent post storage/dedupe;
- bounded retry/backoff;
- preserved last-good cursor on 401/403/429 exhaustion/parse failure;
- explicit lower-bound or terminal evidence;
- global cursor advancement only when every required active source has complete proof;
- no Telegram delivery and no production-state mutation in validation prototypes.

No new scraper, proxy, browser bypass, cookie-transfer service, or paid API was installed during this cycle without evidence.

## SQLite research applied to #136

Research confirmed that `wal_checkpoint(TRUNCATE)` can complete imperfectly or return a busy state when readers/locks are present. Therefore "PRAGMA returned without throwing" is not enough for attestation.

A zero-dependency external pattern (`sqlite-checkpoint`) was reviewed because it treats partial/busy checkpoint as automation failure (for example, a "require complete" mode). We adopted the semantics, not the dependency.

No SQLite package upgrade was added merely because concurrency edge cases exist; Hani's current workflow performs its checkpoint after the application process exits, so a new dependency was not justified without direct evidence.

## GitHub write / connector history

During this cycle, many GitHub write attempts were rejected before mutation by the connector safety layer with:

`This tool call was blocked by OpenAI's safety checks. Please double check what you are sending.`

Affected operation types at different points included:

- file update/create;
- low-level blob write;
- issue comment;
- PR creation / workflow-file update.

Important handling rule followed:

- no blocked write was ever reported as completed;
- work continued on safe read/research/validation tasks;
- no force operation was used to bypass the block;
- write was retried in later executions;
- eventually some writes succeeded, producing the real #136 commits and Draft PR #141.

## Production / workflow observations

Across repeated checks:

- Daily, Fanfic, Maintenance, Security, Render validation and Watchdog workflows were inspected before mutation decisions;
- production main remained healthy enough to continue review/delivery even while X completeness was unproven;
- Watchdog recovery chains were observed succeeding on canonical main;
- Telegram delivery succeeded on partial-recovery runs;
- X incompleteness did not incorrectly advance the full-success cursor;
- Fanfic/AO3 isolation remained a separate guarded path and was not modified during this work;
- no production secret was moved to a new host/service merely to bypass X restrictions.

## Tools / dependencies intentionally not installed

The following were not promoted into production merely because they were interesting:

- `codebase-memory-mcp` — kept behind isolated benchmark/adoption gate;
- browser/CDP X bypass stacks;
- proxy rotation/TLS impersonation stacks;
- external scraper stacks whose retrieval completeness was unproven;
- `sqlite-checkpoint` package (semantics copied conceptually; dependency unnecessary);
- pytest (test suite should remain compatible with the repository's unittest contract).

## Branches / PRs created during this cycle

- `fix/database-checkpoint-attestation-136`
  - contains design + early runtime implementation;
  - tracked by Draft PR #141;
  - now stale relative to current main and must not be merged as-is.
- Draft PR #141
  - title: `fix: attest private-review database checkpoint in production outcome`
  - useful historical implementation/evidence, but base is stale.
- `fix/checkpoint-attestation-refresh-136`
  - created directly from current main after #141 diverged;
  - this is the active branch for safe reapplication of #136 against latest product behavior.

## Safety decisions preserved

- no force-merge;
- no force-update of main;
- no weakening of X completeness just to obtain green status;
- no silent cursor advancement on partial/unproven source windows;
- no high-risk scraper/browser/proxy bypass deployment;
- no transfer of X credentials to an unproven new host;
- no new paid API dependency without explicit authorization;
- no production Telegram/AO3 state mutation in validation-only prototypes;
- no direct work on main.

## Immediate next steps

1. Reapply #136 implementation onto `fix/checkpoint-attestation-refresh-136` from current main.
2. Keep checkpoint tests pure `unittest`.
3. Wire `.github/workflows/main.yml` to the repository-owned checkpoint attestation helper.
4. Open a fresh Draft PR from the refresh branch; mark #141 superseded rather than force-refreshing it.
5. Run exact-head CI and resolve only evidence-backed regressions.
6. After merge, capture real-main Daily + independent Watchdog + artifact proof.
7. Close #136 only after production attestation succeeds.
8. Continue #140 separately with an isolated per-source pagination/coverage prototype; do not weaken the current fail-closed completeness engine.

## Handoff rule

Before any future change, re-read:

- current `main` SHA;
- open PRs and exact-head CI;
- #136 and #140;
- `docs/LAUNCH_STATUS.md`;
- `docs/EXECUTION_ROADMAP_V2.md`;
- this worklog.

If any of those differ from the state above, current GitHub truth wins.
