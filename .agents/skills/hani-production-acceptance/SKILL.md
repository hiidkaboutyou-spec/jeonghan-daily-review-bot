---
name: hani-production-acceptance
description: Project-specific acceptance gates for Jeonghan Daily Assistant changes that affect Telegram, X retrieval, translation, media, state, workflows, or other owner-visible production behavior.
---

# Hani Production Acceptance

Use together with `evidence-first-engineering` whenever a change can affect real bot behavior.

## General rule

The acceptance test must exercise the **concrete production path**, not only a helper/base class.

Canonical runtime composition and overrides must be inspected before deciding what to test.

## Telegram / button / message handling

For changes involving Telegram polling, callbacks, reply keyboards, shared links, quick input, or handler dispatch:

1. exercise the concrete production application class used by the GitHub Actions runtime;
2. prove an incoming update reaches the intended handler;
3. prove Telegram offset advances only after successful processing;
4. prove transient failures retain the update for retry without poisoning durable state;
5. prove long-poll/timeout arguments survive every production override/decorator;
6. run the normal repository test suite and Telegram-focused tests;
7. inspect exact-head PR CI;
8. after merge, inspect a real `main` Daily run through the monitor/checkpoint steps;
9. only call owner-visible responsiveness verified after an actual user interaction or equivalent live delivery evidence.

A base-class unit test alone is insufficient.

## Shared X-link ingestion

For pasted/shared X status URLs:

1. test the exact URL forms Telegram can send, including query strings;
2. verify URL detection -> fetch/extract -> Update/Draft construction -> private-review delivery;
3. verify source attribution and timestamps do not become invented facts;
4. verify upstream failure produces an explicit private error/review state rather than silence;
5. verify retries/dedup do not duplicate drafts;
6. if live X access is unavailable, do not claim end-to-end live ingestion is proven.

## X collection / completeness

Workflow success does not prove collection completeness.

For collector changes prove:
- page/window coverage;
- cursor does not advance on partial/failure;
- configured source constraints remain intact;
- fallback behavior is explicit;
- duplicate suppression remains safe.

Use live production evidence only when credentials/network policy allow it.

## Translation

For translation/runtime changes:
- run focused fidelity/naturalness regressions;
- run the exact production writer smoke for EN/KO/JA/mixed paths;
- preserve entity/number/date/speaker/URL/hashtag/emoji/laughter checks;
- distinguish deterministic correctness from human/editorial quality;
- do not call naturalness "improved" from unit tests alone when a human-quality benchmark is required.

## Media and delivery

For media changes verify:
- actual production media dispatch boundary;
- retry/rate-limit behavior;
- delivery receipts/idempotency;
- multipart ordering and keyboard placement;
- optional FFmpeg/gallery-dl paths when touched.

## State / persistence / recovery

For state changes verify:
- backward-compatible load or tested migration;
- SQLite checkpoint/quick-check where relevant;
- restart/reopen behavior;
- cache vs encrypted recovery semantics;
- no concurrent writer authority introduced.

## GitHub Actions/runtime changes

PR CI proves candidate code. Production behavior may still differ.

For changes to runtime entrypoints, workflow scheduling, environment wiring, caching, recovery, or live dispatch:
- require a real post-merge `main` run before claiming production verification;
- inspect the exact steps that exercise the changed code;
- record the run ID in the handoff.

## Final status vocabulary

Use precise labels:
- "implemented on branch";
- "PR-head CI green";
- "merged";
- "post-merge main run green";
- "owner-visible behavior confirmed".

Never jump directly from the first two to "fixed in production".
