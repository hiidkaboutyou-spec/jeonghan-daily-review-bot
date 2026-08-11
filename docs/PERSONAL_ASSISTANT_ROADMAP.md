# Jeonghan Personal Assistant Roadmap

This roadmap treats the bot as one private assistant, not a pile of independent commands.

## Product goal

The admin should be able to open one Telegram chat, speak naturally in Persian, receive complete Jeonghan updates in chronological order, get channel-style Persian captions, review/edit them quickly, search old events, retrieve a source's last 24 hours, replay the last 2 hours, manage fanfic digests and reminders, and understand when a provider is unhealthy — without needing to remember implementation details or GitHub workflows.

Public/channel auto-publishing is intentionally out of scope. The assistant remains a private review tool.

## Layer 1 — Reliable collection — DONE in current main

- scheduled X collection with retry windows
- strict oldest-to-newest ordering
- 2-hour forced replay
- per-source 24-hour retrieval
- archive + external search
- event/thread grouping
- spam/noise rejection and translation-outage deferral

Acceptance gate: missing-provider or translation failures do not cause raw unusable posts to be presented as finished captions.

## Layer 2 — Channel-style translation — DONE in current main

- channel corpus and style memory
- channel-style caption writer as primary path
- hardened fallback behavior
- manual-review mode for uncertain output
- benchmark and quality-gate workflows

Acceptance gate: production uses the channel-style writer when the corpus validates and falls back safely when it cannot.

## Layer 3 — Review workspace — DONE in current main

- private draft inbox
- ready/rejected/pending states
- fun / soft / precise / clean-text actions
- media delivery and Telegram file caching
- long-message splitting
- durable callback data

Acceptance gate: review actions never publish publicly and do not lose the original archived update.

## Layer 4 — Personal assistant interaction — IMPLEMENTED in PR #9

- Persian natural-language intent routing
- persistent assistant keyboard
- assistant dashboard with next recommended action
- ordinary text defaults to archive search instead of `command not recognized`
- natural recent-update, source-24h, archive, inbox, reminder, status, and fanfic requests
- direct Reply feedback on a draft: بامزه‌تر / نرم‌تر / دقیق‌تر / متن تمیز / رد
- pending search/source prompts can be interrupted by navigation without trapping the user

Acceptance gate: the common daily workflow can be completed without typing a slash command.

## Layer 5 — Reliability and operations — EXISTING + VERIFY ON PR

- private SQLite persistence
- cache/recovery workflow
- source health reporting
- bounded Telegram retries and poison-update quarantine
- optional Sentry monitoring
- scheduled workflow smoke checks

Acceptance gate: validation, smoke checks, and existing regression suites pass on the assistant branch before production activation.

## Layer 6 — Production activation — BLOCKED UNTIL VALIDATION PASSES

1. Review PR #9 CI and smoke checks.
2. Fix any regression on `agent/personal-assistant-v1`; do not patch production blindly.
3. Keep PR draft until checks are green and runtime behavior is reviewed.
4. Only then promote the assistant entrypoint to production according to the repository's release policy.

## Daily user experience after activation

- Open bot → `✨ دستیار من` shows what needs attention.
- Say `چه خبر؟` → last 2 hours.
- Say `امروز چی شد؟` → today's archive/X search.
- Say `۲۴ ساعت @source` → complete source window.
- Say `پیدا کن ...` → archive + external search candidates.
- Open `📥 پیش‌نویس‌ها` → review queue.
- Reply `بامزه‌تر` / `نرم‌تر` / `دقیق‌تر` / `متن تمیز` / `رد` to a draft.
- Use `📚 فن‌فیک` and `⏰ یادآورها` from the same assistant keyboard.
- `📋 وضعیت` or `✨ دستیار من` explains health and suggests the next action.
