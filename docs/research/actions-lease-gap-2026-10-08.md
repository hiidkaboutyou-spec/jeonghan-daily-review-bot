# GitHub schedule gaps and webhook delivery credential lease — 2026-10-08

## Observable production incident

- Canonical production branch before this change: `main@cd0d9c5ac8180dd4a3a197bc4d9c3e77d8c1fa83`.
- On 2026-10-08 the recorded Daily Actions live starts included 02:49 UTC and 09:32 UTC, a gap of more than six hours, despite the configured five-minute schedule. A `success` Actions outcome only acknowledges webhook maintenance HTTP 202; it is not an end-to-end delivery receipt.
- The Railway webhook recovered public X results from all 33 selected sources but deferred pending Telegram delivery when the ephemeral Gemini credential was absent. Log evidence includes `Expired in-memory Gemini provider credential lease` followed by `Pending delivery deferred until a translation credential is available`.
- The previously deployed Railway service was still running `e2dc6f041de239c58b212baff051cab4db31a5e5`; on 2026-10-08 it was explicitly reconnected to GitHub `main` and Railway deployment `164cae62-f82a-4702-9527-db19f94c6b0f` succeeded on exact `cd0d9c5...`. This deploy is separate from the code change in this branch.

## Root cause and explicit availability/security choice

The owner-visible problem is a dependency inversion: a long-lived webhook process owns discovery and delivery, but its translation credential only arrives via best-effort GitHub Actions wakes. The 10-minute memory lease is shorter than empirically observed scheduler gaps, so source recovery can work while delivery stalls.

Change only the bounded, memory-only credential expiry from 10 minutes to **8 hours**. This bridges the observed ~6h43 gap while preserving the existing authenticated HTTPS origin pinning, redirect refusal, secret redaction, single-writer delivery architecture, and mandatory translation fidelity verification. The lease is still actively cleared at expiration and automatically lost on process restart. No key is persisted in Railway variables, GitHub code, Telegram cloud backup, SQLite, artifacts, or HTTP health.

**Security tradeoff:** the memory residency window for a leased key is now 8h rather than 10min, so an attacker with process-memory access during that window may have more opportunity to extract it. This is an explicit availability-over-short-lived-memory tradeoff, not a claim that a longer TTL is inherently more secure. Users requiring shorter exposure should retain the old TTL or provision an authorized static Railway credential in its secret manager (this requires a separate owner-controlled secret placement).

This does **not** guarantee availability across gaps longer than 8h, restarts without a new lease, invalid/quota-limited keys, translation fidelity rejection, or blocked Telegram callback processing during long delivery batches.

## Regression and verification contract

- Concrete `WebhookRuntime.maintenance_sync` must flush queued delivery seven hours after a legitimate lease, without repeating the unrelated source scan.
- An expired lease (at configured TTL + 1 second) must clear the key, client, and both writers. The existing test covers this against the TTL constant.
- Fresh leases and static configured keys must retain existing behavior.
- Review full exact-head PR CI, including Daily, tests, security, translation smoke, Docker/runtime, and relevant workflow gates before any merge.
- After merge verify the deployed Railway SHA, Telegram `/healthz` readiness/backlog state, an actual authorized Actions credential wake and private delivery receipts. **Do not call user-visible freshness or button latency fixed without observing real interaction.**

## Rollback

Revert the one constant change in `app/webhook_server.py` to `10 * 60`, keep the existing tests and code safety boundaries, and redeploy after validation. No state schema/cursor, source configuration, production secrets, or new dependencies are involved. Do not restart Railway merely to obtain a credential: a restart intentionally discards the in-memory lease.
