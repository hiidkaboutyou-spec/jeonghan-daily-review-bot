# Date content requests and translation repair

A date request now creates a durable private bundle rather than eight search candidates. Use `/date 261004 لایو تولد`, `/date 20261004`, or ordinary Persian such as `محتوای جونگهان ۲۶۱۰۰۴ رو بفرست`. `/date_status` shows progress. Date-picker and `/search` date arguments enter the same bundle path. Invalid dates do not become unbounded searches. Future dates are rejected.

Event days use `runtime.content_date_timezone`, configured as **Asia/Seoul**; timestamps are converted to half-open UTC bounds. This is explicit in the acknowledgement. The general UI timezone remains Asia/Tehran. Supported scopes are the whole date and live coverage. Live selection requires an explicit multilingual live marker; other posts from the same author and conversation are included as thread context. A plain Weverse text post is not evidence of a live. Additional free-form topic filtering and private Telegram-channel ingestion are not implemented here.

## Collection and organization

Every enabled configured X source is snapshotted and attempted. Local SQLite date windows stream all records without the search limit or FTS translation mismatch. The archive and collector both pass the existing source-authority/mode gate. Duplicate post IDs are removed. A complete source timeline remains the primary retrieval method; existing public syndication/Agent-Reach recovery stays partial. When authenticated timeline collection fails, an author-scoped search can contribute partial observations. Search limits and public-profile results never establish complete-day coverage. Disabled sources are not retrieved or delivered.

All sources finish their attempt before delivery starts. The bundle overview lists categories and per-source coverage. Each individual source post then uses the existing translated private-review caption, original source URL, and media path. Ordering is global post-publication time, with ID as a stable tie-breaker. It is **not** claimed to be the original order of moments within the broadcast. Repeated coverage by different authors is retained with attribution; distinct interpretations are not fused into invented dialogue.

The existing webhook worker or Actions runtime owns the queue; no second poller, writer, external chat target, or new paid provider is introduced. Each maintenance wake processes at most five steps and starts no further step after a 25-second work budget; a single in-flight collector can additionally consume its timeout (25 seconds for timeline, 20 seconds for search recovery). Existing synchronous model/media calls retain their existing timeout behavior. The Actions interactive tail also advances jobs when at least 60 seconds remain. Actual latency depends on source/provider availability and the existing host.

Jobs, coverage, observed IDs, selected IDs, and acknowledged deliveries survive state reload and cloud backup. SQLite retains observations even if the JSON archive reaches its pruning cap. Request draft IDs and Telegram receipt keys are deterministic. Interrupted caption sends reuse persisted translations. Explicitly repeating a partial request retries incomplete sources and pending translations; already confirmed posts are skipped. Recovered posts form a chronologically sorted supplement; earlier Telegram messages cannot be retroactively reordered.

## Requests from ChatGPT

`runtime.repository_content_requests: true` enables the fixed trusted-main queue `config/content_requests.json` in this repository. The existing bot checks it at most once per five minutes. A ChatGPT session with repository write access can add a reviewed public entry, merge it to main, and have the existing bot deliver to its configured private review chat:

```json
{"id":"rey-261004-birthday-live","date":"2026-10-04","topic":"live"}
```

Only these three fields are accepted. IDs are bounded; dates are validated; `topic` is `all` or `live`. There are no chat IDs, tokens, source text, arbitrary URLs, or executable instructions in this public queue. An ID is accepted once across restarts. A new public ID requests a new bundle; repeating a bot date request retries that bot request. A branch or draft PR is not a live dispatch. This is a repository bridge, not automatic access to other ChatGPT conversations or Telegram accounts.

## Translation root cause and repair

Issue #158 identified repair requests that omitted hard-fact failures. A regression against the previous installed production writer confirmed that translating `Jeonghan ate 2 apples.` with an invented 3 produced **an empty repair failure list**. If repair failed, the finalizer only reran semantic checks and could omit the manual-review flag for this hard-number failure.

The repair payload now contains deduplicated hard-fact, entity, and semantic failures using the complete attributed `translation_source()`. The finalizer reruns the same gates. Existing natural Persian instructions preserve register, numbers, speakers, emoji, negation, and quoted attribution. Date bundles defer missing captions, model-outage placeholders, and manual-review candidates instead of falling back silently to original English/Korean/Japanese text. Translation retries back off; after three unsuccessful attempts the bundle reports pending translation and retains its place. A repeated date request retries it. This does not manufacture API quota or prove every live model output is fluent.

## Research decision

Primary sources inspected:

- [twscrape](https://github.com/vladkens/twscrape): authenticated search/timelines, streamed/raw responses, account rotation. Retain the repository's pinned implementation and completeness proofs.
- [Agent Reach](https://github.com/Panniantong/Agent-Reach): reuse the already pinned recovery integration; do not treat broad marketing claims as complete historical X coverage.
- [Telegram Bot API](https://core.telegram.org/bots/api): keep existing private transport and receipt/retry handling.
- [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output): schema-conforming JSON is not sufficient evidence of translation fidelity, so retain deterministic source-grounded verification.

FxTwitter discovery proposals and an operating-system switch were not promoted to completeness authorities. Existing issue #140 records cross-runner authenticated-X failures; this feature exposes partial coverage rather than claiming a new library alone solves that provider outage.

## Verification and rollout

Regression tests cover compact/Persian dates, the real admin handler and date-search entrypoint, full archive windows exceeding 500 posts, both configured sources, source filtering, global ordering, partial recovery, attributed thread extension, invalid dates, missing translations, crash/restart send recovery, and trusted-main queue acceptance across restart. Installed-writer tests demonstrate both the previously failing repair-payload and hard-failure finalizer cases.

Local validation: requirements install, pip check, compileall, `python -m app --check`, and the full unittest suite. PR/main CI and owner-visible live delivery must be recorded separately; local mocked runtime tests do not establish that Railway has deployed this revision, that X is accessible, or that Gemini quota is available.

Rollback: revert this change and set `repository_content_requests` false. Durable date-job state and existing archive rows can remain; no daily source policy or transport secret needs to be changed.
