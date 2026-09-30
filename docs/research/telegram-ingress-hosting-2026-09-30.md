# Telegram ingress hosting decision — 2026-09-30

Issue: #151  
Implementation PR: #152

## Observed failure

The reply-keyboard labels and Persian intent routing are present in the production application. The owner-visible outage came from transport availability instead:

- the production workflow was forcing `ASSISTANT_RUNTIME_MODE=github_actions_polling`;
- the polling consumer only exists while a GitHub Actions runner is alive;
- real scheduled runs had multi-hour gaps on 2026-09-30, so button messages stayed queued even though the handler code was valid;
- PR #145 reduced latency only while a runner was already alive, and PR #147 fixed its separate timeout-signature crash.

GitHub's schedule event documentation explicitly warns that scheduled workflows can be delayed during high load and that some queued jobs may be dropped:
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule

## Transport ownership implemented in PR #152

The Actions runtime now has two explicit modes:

- `github_actions_auto` — default. If Telegram reports no webhook, Actions may use the existing bounded polling fallback. If a webhook exists, Actions delegates maintenance to it and never deletes/races it. If webhook ownership cannot be inspected or its maintenance endpoint is unavailable, auto mode fails closed instead of starting a second update consumer.
- `github_actions_polling` — emergency rollback only. A manual workflow dispatch can explicitly select this mode, which removes the webhook without dropping pending updates and reclaims polling.

Exit code 3 means maintenance was successfully delegated to the webhook owner and is treated as a successful Actions run. Exit code 4 means webhook ownership was retained but its maintenance path was unavailable; CI reports the failure without starting a competing poller.

The bounded polling tail from PR #145 remains enabled in both Actions modes whenever polling is actually the owner.

## Hosting research

### Render Free — supported fallback, not final responsiveness target

Official docs say a free web service spins down after 15 minutes without inbound traffic, takes about a minute to wake, and has an ephemeral filesystem. A Telegram click can wake it, but the first click after idle cannot meet the <=2 second responsiveness target.

Sources:
- https://render.com/docs/free
- https://render.com/docs/faq

The connected Render workspace had no existing service at the time of this investigation, so there was no live webhook host to repair or restart.

### Northflank Developer Sandbox — preferred zero-cost no-sleep candidate for this personal bot

Northflank currently advertises its Sandbox with always-on compute/no sleeping and two free services. Its docs also state that a payment method is required to create resources and that the free tier is intended for testing rather than production/SLA workloads.

Sources:
- https://northflank.com/pricing
- https://northflank.com/docs/v1/application/billing/pricing-on-northflank
- https://northflank.com/docs/v1/application/run/run-containers-and-micro-services

PR #152 makes the existing Docker runtime directly usable there:
- `EXPOSE 8000` allows Docker-port discovery;
- `NF_HOSTS` is auto-detected as the webhook public origin;
- the existing FastAPI webhook, single worker, durable Telegram offset, retry-safe acknowledgement, and Telegram cloud-state backup stay unchanged.

Northflank documents `NF_HOSTS` as a comma-separated list of public DNS entries:
https://northflank.com/docs/v1/application/secure/inject-secrets

### Cloudflare Workers — deferred

Cloudflare is attractive for edge availability, but the current assistant owns Python/SQLite state and multiple provider integrations. Moving only ingress to a Worker would add a second queue/bridge/state boundary; moving the whole runtime would require a larger durable-state migration. That is not justified for this incident while the existing Python webhook already has the required correctness semantics.

### Paid always-on Render — simplest paid alternative

A continuously available Render service can reuse the current `render.yaml` and FastAPI runtime with the smallest operational change. It is not selected automatically because it introduces an ongoing hosting cost.

## Minimum host configuration

The webhook process requires these three secrets:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ADMIN_USER_ID`
- `TELEGRAM_REVIEW_CHAT_ID`

`X_COOKIE` and `GEMINI_API_KEY` are optional: missing/invalid X credentials disable authenticated X collection rather than taking down the Telegram assistant, and missing Gemini credentials use the existing safe fallback behavior.

For Northflank, connect this repository, build the root `Dockerfile`, expose port 8000 publicly, and add the three Telegram values as runtime secrets. `NF_HOSTS` supplies the public URL automatically.

## Acceptance still required after merge

Do not call #151 fixed merely because PR CI is green.

Required live evidence:

1. deploy an always-available webhook host with the real Telegram secrets;
2. observe startup log `Telegram webhook registered ...`;
3. verify a post-merge Actions run exits through delegated webhook ownership without deleting it;
4. press an actual owner button and confirm the callback/message is handled promptly;
5. restart the host and confirm state restore plus idempotent Telegram offset behavior.

Until those steps are complete, the correct status is “transport handoff implemented and verified in CI; live host provisioning/owner-visible acceptance pending.”
