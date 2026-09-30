# Telegram button latency investigation — 2026-09-30

## Owner-visible symptom

Telegram reply-keyboard and inline-keyboard actions can sit for minutes before the private assistant reacts. A concrete example is pressing **🗂 ۲۴ ساعت منبع** and seeing the user message arrive in Telegram while the bot stays silent for several minutes.

## Root cause in this repository

The production runtime is GitHub Actions only. The main workflow is scheduled at GitHub's minimum cron cadence, roughly every five minutes. Each process currently:

1. restores state and installs/validates dependencies;
2. calls Telegram `getUpdates` once with `timeout=0`;
3. runs the scheduled X scan and pending delivery;
4. exits.

That means a button or message sent after the one-shot `getUpdates` call is not observed until a later Actions run. The user-facing delay is therefore dominated by **cron + runner startup**, not the keyboard itself.

## External implementation research

### Long polling

The maintained `python-telegram-bot` project uses a positive `get_updates.timeout` for polling (its current polling default is 10 seconds), and its raw API example also keeps calling `get_updates` with a positive timeout. Aiogram uses the same pattern through `GetUpdates(timeout=polling_timeout)`.

This fits the current architecture because it:

- keeps Telegram as the update queue;
- needs no new dependency;
- does not create a second state writer;
- does not require a public endpoint or a new secret;
- can be bounded so it does not turn the X/Gemini monitor into an unbounded loop.

### Webhook/serverless alternatives

GitHub contains working Telegram webhook templates for Cloudflare Workers and FastAPI/aiogram. Telegram's Bot API also supports authenticated webhooks through `secret_token`.

A webhook is the correct long-term architecture for near-instant 24/7 interaction, but adopting it now would add another production runtime, secret distribution, deployment/recovery paths, and coordination with the existing SQLite/Actions state. A Cloudflare Worker could safely be a future thin interaction router, but it should not be introduced as an unreviewed state authority.

## Decision for this stage

Use a **bounded Telegram-only long-poll tail window** inside the existing GitHub Actions runtime.

- The normal one-shot poll at process start remains unchanged.
- After the scheduled scan and pending delivery finish, a GitHub Actions run stays available for up to 240 seconds and long-polls Telegram in at most 20-second slices.
- The X scan and Gemini work are **not** repeated during this window.
- Telegram offsets and awaiting state are checkpointed after each interactive batch.
- A hard application-runtime cap (default 13 minutes, maximum 14) prevents this responsiveness window from consuming the existing 15-minute runtime step after a slow monitor pass.
- Outside `ASSISTANT_RUNTIME_MODE=github_actions_polling`, the extra window is disabled.

This should remove most multi-minute waits while preserving the current single-runtime/single-state-writer model.

## Remaining limitation

This is not a mathematical guarantee of sub-second response. There can still be a short gap while a fresh GitHub runner is provisioning or while a long explicit admin action is executing. If production evidence still shows unacceptable gaps, the next stage should be a dedicated Telegram webhook interaction plane, with the Actions monitor remaining the heavy background worker.

## Acceptance criteria

- existing one-shot polling behavior remains compatible;
- positive long polling is covered by tests;
- the interaction window is Actions-only and bounded;
- state is checkpointed after every interactive batch;
- no extra X/Gemini scan is triggered by idle polling;
- all existing validation workflows remain green before merge.
