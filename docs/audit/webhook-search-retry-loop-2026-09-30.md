# Webhook search replay loop

Owner symptom: searching @haniwadda repeatedly emitted the archive-search progress message.

## Verified cause

Preflight main: d217281cfeff887547a72906e40d89fb3e02691a (#159). Reviewed current open PRs, AGENTS.md, evidence-first and production-acceptance skills, the source handoff, concrete WebhookAwarePersonalAssistant message dispatch, PrivateReviewApplication.run_search, polling safety, StateStore failure persistence and WebhookRuntime. Local Project Memory config is absent.

Railway webhook deployment fbcdc735-35d4-491c-a834-ed5a4df8fd20 was still running main@97e77ce. Runtime logs from 19:21–19:30 UTC showed the same incoming update repeatedly exhausting XCollectionError retries, returning HTTP 503, then entering another group of three searches. No private command text, credentials or raw Telegram payload is retained here. GitHub Actions green runs did not describe this live failure.

Empty archive plus XCollectionError was re-raised after the progress message. Webhook retried the whole handler three times per HTTP delivery, with no persisted application-failure budget. Telegram then retried the non-2xx webhook. Official reference: https://core.telegram.org/bots/api#setwebhook documents retries after non-2XY responses. No new external library is needed; reuse the existing polling poison-budget mechanism.

## Change and evidence

- Search treats unavailable X as an explicit incomplete search outcome, sends a private explanation, and returns successfully only after that message is sent. A Telegram delivery failure still propagates for retry.
- Webhook provider/unexpected application faults use the existing persisted three-attempt budget, once per HTTP delivery. It survives reopen/restart and quarantines the offending command so later commands proceed. Telegram transient/platform errors retain their prior retry/offset policy; permanent/configuration errors retain prior handling.
- The new tests use the concrete WebhookAwarePersonalAssistant message/search dispatch inside WebhookRuntime with an offline failing collector. Duplicate delivery is silent after durable acknowledgement. A second regression verifies poison-budget persistence through three StateStore reopen cycles and processing the next update.
- Before fix, both regressions failed for the expected reasons (false acknowledgement / three handler calls). After fix: 32 related tests passed, pip check passed, compileall passed, app --check reported 33 sources, full suite passed 1,314 tests (6 skipped) on Python 3.12. CI supplies target Python 3.11 verification.

No dependency, provider, credential, source mode, cursor or state-schema changes. Search availability and whole-window X completeness remain separate unresolved concerns. Rollback: revert this PR. Release acceptance: exact-head CI, merge, redeploy the existing Railway webhook owner, confirm deployment revision and absence of the old repeated 503/search loop. Owner-visible behavior needs fresh live interaction or equivalent pending-update delivery evidence.
