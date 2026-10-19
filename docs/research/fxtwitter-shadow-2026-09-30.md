# FxTwitter v2 shadow completeness gate — 2026-09-30

## Goal

Determine whether FxTwitter API v2 can replace part of the currently degraded X retrieval path **without** giving it delivery or state authority first.

The current production blocker is not formatting or Telegram delivery. Authenticated X retrieval has already failed on all three GitHub-hosted runner families with HTTP 403, and the production fallback remains partial. A replacement is only useful if it can prove the requested source window rather than returning a plausible but incomplete sample.

## Why FxTwitter is the first shadow candidate

The current FxEmbed implementation is active, MIT-licensed and self-hostable. Its API v2 exposes a profile-status timeline with:

- page size up to 100;
- opaque bottom-cursor pagination;
- optional replies;
- structured JSON status timestamps;
- a public rate limit documented as 1000 requests/minute/IP.

Upstream itself implements multi-page walking over `cursor.bottom`, which is stronger evidence than a single embed/status endpoint.

## Conservative proof contract

The probe reads every enabled project source and never touches Telegram, archive state, project cursors, checkpoints, secrets or delivery.

A source is marked complete only when:

1. FxTwitter exhausts the cursor; or
2. non-repost timeline rows stay chronological and cross below the requested lower boundary.

Reposts cannot prove the boundary because their exposed timestamp can describe the original post rather than the repost event. Malformed rows, repeated cursors, HTTP errors and the per-source page budget fail closed.

The workflow exits non-zero unless **every configured source** proves the window. The JSON evidence uploads even on failure.

## Promotion rule

A green shadow run is necessary but not sufficient for production adoption. Before provider promotion:

1. inspect per-source evidence for suspicious empties/schema changes;
2. compare a due window against the existing partial paths and known posts;
3. implement source-authority/keyword-mode filtering using existing project rules;
4. run no-delivery exact-head validation;
5. only then allow it into recovery, initially still without advancing the authoritative full-success cursor.

## Upstream references checked

- FxEmbed API overview: `docs/src/content/docs/api/introduction.mdx`
- FxTwitter route: `src/realms/api/routes.ts` — `GET /2/profile/{handle}/statuses`
- FxTwitter profile pagination implementation: `packages/atmosphere/src/providers/twitter/userStatuses.ts`
- FxEmbed repository: MIT license, active main branch as checked 2026-09-30.

## First live shadow result and correction

The first exact-head live probe reached all 31 enabled sources. It proved 7 immediately,
reported 19 partial at a six-page diagnostic budget, and saw 5 HTTP 404 outcomes. Four
of those 404s occurred only after successful timeline pages had already been returned.

Reviewing FxEmbed's own current `paginateAndMerge` implementation showed that upstream
treats a 404 **after prior successful pages** as a normal pagination end, while a first-page
404 remains ambiguous/not-found. Upstream also uses a ten-page maximum for its own
profile-status pagination helper. The gate was therefore corrected to mirror those two
upstream semantics; first-page 404 remains a hard failure and malformed data still fails closed.
