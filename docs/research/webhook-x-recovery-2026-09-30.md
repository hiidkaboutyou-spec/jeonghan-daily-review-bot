# Webhook X recovery research — 2026-09-30

Issue: #156

## Production symptom

Telegram ingress is now healthy on Railway, but the owner-visible `🕑 ۲ ساعت اخیر` flow can still return no updates with a partial-X warning.

The successful webhook request proves this is no longer a Telegram transport problem. The always-on Railway service currently has no X `auth_token` / `ct0` cookies, while the authenticated collector requires both.

## Root cause

Hani already has the right recovery pieces, but they were composed differently between the two runtime entrypoints.

### GitHub Actions / Daily

`app.sentry_runtime` installs `x_degraded_recovery_runtime` on the final `WebhookAwarePersonalAssistant`. That layer adds the FxTwitter v2 public timeline fallback and outcome hardening.

### Railway webhook

Railway starts `app.webhook_server:api` directly. Before #156, that module imported the same application class but did not install `x_degraded_recovery_runtime`.

The core `x_provider_recovery` layer was present, but its switch depended entirely on the external environment variable `X_PROVIDER_PREFLIGHT=degraded`. A webhook process with no cookies and no Actions preflight could therefore try a collector that was deterministically unable to authenticate.

## GitHub / upstream research

### 1. FxEmbed / FxTwitter API v2 — adopted existing provider

Repository: https://github.com/FxEmbed/FxEmbed

Current upstream route:
`GET /2/profile/{handle}/statuses`

The current OpenAPI/route implementation documents:
- page size up to 100;
- opaque `cursor` pagination;
- optional `since` timestamp;
- optional `with_replies`;
- structured `results` + `cursor` envelope.

Relevant upstream files:
- `src/realms/api/routes.ts`
- `src/realms/api/routes/twitter.ts`
- `packages/atmosphere/src/providers/twitter/userStatuses.ts`
- `docs/specs/fxtwitter-openapi.json`

Hani already has `app/x_fxtwitter.py`, which validates configured author identity, rejects reposts, enforces the requested time window, preserves replies according to source policy, and converts media into the existing `Update` model.

### 2. Hani's own live FxTwitter shadow evidence

PR #143 ran a read-only 24-hour completeness probe against all 31 configured sources.

Result:
- 31/31 sources reached the provider;
- 0 provider errors;
- 7 sources conservatively proved the full 24h boundary;
- 24 were marked partial because the ten-page proof budget was exhausted before a lower-bound witness was observed;
- the partial sources still returned real rows, including many rows inside the requested window.

This is sufficient evidence to use FxTwitter as a **partial recovery provider**. It is not sufficient to grant it authenticated full-success cursor authority.

### 3. ythx-101/x-tweet-fetcher — architecture reference, not dependency

Repository: https://github.com/ythx-101/x-tweet-fetcher

Reviewed current v3.1.0:
- MIT;
- Python 3.10+;
- stdlib-only core;
- multiple read backends with explicit fallback;
- FxTwitter for public data, Nitter for timeline/search, browser fallback;
- machine-readable provider failures.

Useful idea adopted: provider failover should be explicit and should never turn an upstream failure into a silent empty success.

Rejected as a dependency because Hani already has:
- its own source authority model;
- `Update` normalization;
- Telegram/media integration;
- completeness/cursor rules;
- existing FxTwitter/syndication/Agent Reach providers.

Adding the package would duplicate authority and state logic.

### 4. Nitter — rejected as the first repair

Repository: https://github.com/zedeus/nitter

The connected GitHub metadata currently reports the upstream repository as archived. x-tweet-fetcher also warns that public Nitter instances are unreliable and recommends self-hosting.

Self-hosting another service would add session/proxy/runtime maintenance and still would not solve Hani's existing entrypoint-composition bug. It is therefore a weaker first repair.

### 5. Twikit / authenticated scrapers — not a no-secret solution

Repository: https://github.com/d60/twikit

These approaches still rely on authenticated X session/account material for the reliable timeline path. Railway currently lacks those cookies, so changing Python clients would not remove the blocker.

### 6. Single-post syndication/download projects

Reviewed current no-login projects using X's public syndication surfaces, including:
- https://github.com/anhao/x-video-download
- https://github.com/Krainium/X-Downloader

They reinforce the existing choice for explicit shared-link ingestion and media recovery, but single-post syndication is not a substitute for monitoring 31 account timelines.

## Implementation

### Automatic degraded detection

`x_provider_recovery._degraded()` now accepts the collector instance. Recovery mode is selected when either:

1. `X_PROVIDER_PREFLIGHT=degraded` explicitly says so; or
2. the collector does not have both required authenticated-X cookies (`auth_token` and `ct0`).

This is fail-fast capability detection, not a health guess. A collector literally cannot run the authenticated path without those credentials.

Authenticated deployments with both cookies and no degraded signal continue using the primary collector.

### Same provider hardening on webhook runtime

`app.webhook_server` now explicitly installs `x_degraded_recovery_runtime` on `WebhookAwarePersonalAssistant`, matching the production Daily composition.

This makes the existing chain available on Railway:

authenticated X unavailable
→ public profile syndication
→ FxTwitter v2 fallback
→ bounded Agent Reach fallback when usable

The public result remains partial and cannot advance the authenticated full-success cursor.

## Current live Railway mitigation

Before this branch merges, Railway was also explicitly set to:

- `X_PROVIDER_PREFLIGHT=degraded`
- `X_SYNDICATION_FALLBACK_BATCH_SIZE=31`
- `X_SYNDICATION_FALLBACK_CONCURRENCY=8`

This activates the existing public path immediately while the code-level automatic detection is validated.

After the code repair is merged and owner-visible behavior is proven, the explicit provider-state variable can be removed; the missing-cookie detection is then sufficient.

## Safety boundaries

Unchanged:
- configured-source authority;
- no arbitrary URL fetch;
- admin-only Telegram interaction;
- no automatic public publishing;
- no cursor advancement from public fallback;
- no new secret;
- no new dependency;
- no state schema migration;
- existing 1080p/720p media quality policy.

## Validation contract

Before merge:
1. focused provider-recovery regression;
2. webhook wiring regression;
3. canonical project validation;
4. exact-head CI on all repository gates.

After merge:
1. Railway deploy on exact main commit;
2. webhook remains 2xx;
3. press `🕑 ۲ ساعت اخیر`;
4. inspect provider logs and delivered updates;
5. keep the completeness warning if public recovery cannot prove the full source window;
6. do not label automatic X completeness restored merely because useful partial updates are delivered.

## Rollback

Revert the focused PR and remove the explicit Railway recovery variables if necessary. No database/state migration is involved.
