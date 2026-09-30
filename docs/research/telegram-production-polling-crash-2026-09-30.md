# Telegram interactive polling production regression — 2026-09-30

## Verified incident

Preflight confirmed canonical `main@f191a8c1453eefbdd7ed5f9d0af567c02dc74f5c`
(PR #145). Open PRs and `docs/LAUNCH_STATUS.md` were reviewed; the latter's
older production SHA is historical evidence, not current health. Machine-local
Project Memory was unavailable; current GitHub evidence was used.

Daily run [36697182750](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/36697182750)
passed validation and provider preflight, then failed in the monitor step:

```text
TypeError: TelegramSafeReviewApplication.process_telegram_updates() got an unexpected keyword argument 'long_poll_seconds'
```

The base `Application` introduced the interactive tail but the concrete
`WebhookAwarePersonalAssistant` inherits the retry-safe polling override from
`TelegramSafeReviewApplication`. That override did not accept the new argument.
The original tests mocked the override or exercised only the base class, missing
the production method dispatch. The four-minute window therefore crashed before
it could poll. A successful validation job did not prove live responsiveness.

## Repair and regression proof

The production override now accepts the optional long-poll timeout and forwards
it to the existing Telegram transport. Its ordered processing, transient-failure
retry, poison-update quarantine and offset rules remain intact. One-shot callers
retain the original transport call.

Two tests exercise the concrete production class's inherited interactive window
with the real safe update processor and a temporary durable StateStore. They
failed with the exact production TypeError before the repair. They cover handler
execution before offset advancement, persistence after success, and retained
offset/no poison counter on transient failure. Network transport and the handler
are mocked; production credentials and live delivery are not used.

No dependency or hosting change is needed for this demonstrated code defect.
The earlier long-poll research remains applicable. Actions scheduling/startup
gaps and slow explicit handlers still limit responsiveness; this repair does not
claim continuous availability or complete X retrieval.

## Acceptance and handoff

Run repository compile, configuration, dependency and full unit checks, then
require CI on the final PR head. After merge inspect a real Daily run on the new
main: it must enter the Telegram interactive window without TypeError, finish
the monitor and state checkpoint/cache steps. Record that run before declaring
the runtime repaired. Human-visible button responsiveness remains a separate
owner check; do not infer it solely from green CI.

Rollback is the isolated repair commit; no schema, secret, provider or delivery
authority migration is involved.
