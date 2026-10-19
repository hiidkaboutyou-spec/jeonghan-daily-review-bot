# @hanniezones source-only backfill request — 2026-10-19

## User-visible goal
Send all discoverable updates authored by `@hanniezones` across 2026-10-16 through 2026-10-19 (inclusive), with private Telegram Persian translations and source links. No other source should be included in these request bundles.

## Repository changes
- Add `hanniezones` to the configured priority full-feed sources with replies enabled. This is required by the repository bridge's enabled-source authority check.
- Add one trusted-main `config/content_requests.json` bridge entry per date, with distinct once-only IDs `rey-hanniezones-20261016` through `rey-hanniezones-20261019`, `topic=all`, `source=hanniezones`.
- Reuse the existing translation, source filter, archive/delivery receipts, deduplication, day-bundle ordering and /date_status path. No new Telegram destination, credential, automated public publishing, scheduler or collector dependency.

## Coverage and acceptance
- Date bundles use `Asia/Seoul` day boundaries (not an exact 72-hour window); 2026-10-19 is provisional until that Seoul calendar day ends.
- Current collector excludes retweets; `full_feed` covers own posts and replies, not necessarily all account activity.
- Do **not** call this complete unless all four source windows have proven complete timelines and the existing private review chat has acknowledged delivery. A passing workflow is not sufficient.
- The bridge is processed no more than every five minutes **when the existing runtime operates**; requests remain queued if it does not run.

## Blocking live evidence (pre-change)
- `main@90c65ac34ae9e1f41ac625cec9d1218f5e5080f2`.
- GitHub Actions run [39211656491](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/39211656491), 2026-10-19: preflight Telegram OK, Gemini OK, authenticated X degraded (HTTP 403); live runtime exited code 4 because webhook maintenance returned HTTP 404. Watchdog success did not mean Daily succeeded.
- Earlier `docs/LAUNCH_STATUS.md` documents the known X 403 completeness barrier. Neither editing this queue nor green CI fixes provider access or webhook routing.

## Remaining steps
1. Validate JSON and existing date/source regressions in PR-head CI.
2. Merge only if applicable required checks pass; re-read canonical HEAD first.
3. Verify an actual post-merge `main` runtime reads the new four IDs; inspect the run, coverage and Telegram receipts.
4. Repair the separate webhook maintenance 404 and authenticated X 403 using a dedicated evidence-driven change before claiming the result delivered/complete.
5. Once providers recover, re-request any partial days via the bot `/source hanniezones 20261016 20261019`; the existing source-only scoped job/receipt mechanism prevents repost of acknowledged items.

Rollback: revert the priority source entry and trusted-main queue entries only after verifying pending/accepted jobs. Preserve private state/receipts.
