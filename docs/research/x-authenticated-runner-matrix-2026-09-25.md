# Authenticated X access: runner evidence gate (2026-09-25)

## Production finding

On `main@88578314a291008f3fdc306485e5b020ac1ae66c`, scheduled Daily [run 36152747092](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/36152747092) finished successfully but reported `recovery_required`: 31 sources attempted, 0 complete, 31 partial, full-success cursor held. The live preflight was degraded. Its twscrape request on Ubuntu returned `HttpStatusError 403` for `UserByScreenName` with backend `httpx` and no proxy. The public syndication recovery still fetched 30 updates from 31 selected sources; private delivery succeeded. The provider problem is authenticated collection completeness, not a total absence of posts.

An earlier post-merge Daily [run 36138271621](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/36138271621) was not due and correctly reported 0 attempted and no complete collection. The prior outcome bug is fixed in production.

## External options and decision

| Candidate | Evidence / constraint | Decision |
| --- | --- | --- |
| `vladkens/twscrape` v0.20.1 | MIT, Python 3.11; upstream improved directly linked responsive-web script detection after the project's pinned 0.20.0 + 16-hex fix. The production failure is HTTP 403 on an authenticated request, so this is not proven to restore access. | Pin to exact upstream commit `5271cbdc5da1095b765a2a7ec750b4ac686d581f` **in the diagnostic only**; do not promote production dependency yet. |
| GitHub-hosted runner OS matrix | Different hosted OS/network routes might behave differently. Old PR #64 only probes anonymous pages and uses an unpinned checkout; anonymous reachability does not prove authenticated GraphQL access. | A single, read-only, authenticated profile request on Ubuntu, macOS, Windows using the existing GitHub secret. Serial execution and coarse results only. |
| Official X API | Official timeline reads use pay-per-usage credits; this project has no scoped API credential, price authorization, or new data-recipient review. | Do not activate or spend credits. |
| XActions, browser session scripts, public mirrors | Browser-session dependence or partial public timelines do not establish complete per-source windows on a GitHub runner. The existing syndication recovery already provides partial updates. | No new installation or cursor promotion. |
| YouTube, Instagram, Telegram, X discussions | Searched for a reproducible, maintained GitHub Actions fix; no independently verified source demonstrated authenticated retrieval for this repository's cookies and runner network. Cross-posts on other networks are not completeness proof for X. | Use measured repository evidence before promotion. |

## Probe boundary and acceptance

This workflow is triggered only on canonical `main` when its diagnostic files change, or by explicit workflow dispatch. It does not run with secrets on PRs. It installs a pinned MIT upstream release in disposable hosted runners; `X_COOKIE` remains a GitHub Actions secret and is parsed only in process. One public profile lookup per OS is read-only, serialized, timed out, and isolated in a temporary SQLite directory. No Telegram token, private review state, X response body, cookie value, or raw error text is printed or persisted. The status output is limited to `verified`, `http_403`, `http_401`, `rate_limited`, `transaction_id_unavailable`, `timeout`, `missing_credentials`, or `other_failure`. Diagnostic failures do not block existing production jobs.

**Promotion gate:** a successful authenticated profile lookup on another runner is necessary but insufficient. Before changing the Daily runner, validate full source/keyword retrieval on that OS with private delivery disabled, ensure FFmpeg/state/cache compatibility, pass exact-head CI, and then prove a due real-main window with 31/31 complete and cursor advanced. If all OSes fail, stop the matrix and keep the current partial syndication fallback; use an owner-approved official API budget or trusted host strategy, not an unverified bypass or a forced cursor advance. Rollback: remove this workflow and script; no migration or persistent production state change.
