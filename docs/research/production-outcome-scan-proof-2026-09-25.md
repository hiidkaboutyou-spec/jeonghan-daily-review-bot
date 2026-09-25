# Daily source outcome: scan proof (2026-09-25)

## Verified production evidence

Pre-change canonical `main@d6d876e2c5ce595832675eb0f314db1ead4dceef`: Daily runs [36135819645](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/36135819645) and [36135588995](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/36135588995) completed successfully, while their redacted production-outcome artifacts reported `31/31` sources complete, `collection_complete=true`, `cursor_advanced=false`, and `cursor_not_advanced` (degraded). Another run [36122449834](https://github.com/hiidkaboutyou-spec/jeonghan-daily-review-bot/actions/runs/36122449834) reported a genuinely incomplete window. A green workflow alone therefore does not establish X retrieval.

`WebhookAwarePersonalAssistant.run_scheduled_scan` returns at its cadence guard before calling the collector. The outcome hook previously counted every enabled source complete whenever `last_errors` was empty, even when the scan never started; old collector errors could also survive a skipped scan. The hardened degraded-X classifier treated the resulting 0/31 proof as a missing coverage window. Neither inference is valid for a not-due scan.

## Decision and exit criteria

- Record configured/active totals on every run. On a skipped scan, record **zero attempted**, no complete collection, and `not_due_or_no_advance`; do not request recovery merely for the cadence skip.
- When a scan starts, count sources complete only when the durable `last_auto_run` cursor advances. A held cursor remains incomplete, including when the collector supplies no error details. An explicit `X_PROVIDER_PREFLIGHT=offline` still reports the provider-wide failure.
- Preserve degraded-provider rotating-batch reconciliation, private review delivery, cursor/state writes, and watchdog boundaries. No new dependency or external data recipient is warranted for this local evidence error.
- Regress not-due runs with empty/stale errors, complete empty windows, unproven started windows, and offline preflight. Run full tests and exact-head CI before merge; verify a new real-main Daily outcome after merge. Rollback: revert this focused PR; no state migration.

## Remaining frontier

The outcome artifact is produced before the workflow's database checkpoint step; `database_checkpoint_success` remains a default `false` and cannot attest that later step. A separate workflow-to-watchdog proof design is needed before relying on this field. The old #64 runner egress PR does not prove authenticated X retrieval and requires fresh-head CI and security review. #131 memory MCP remains a draft isolated benchmark; do not install unbenchmarked dependencies. For true X collection completeness, examine per-source retrieval/ledger evidence on a **due** window and the retained cursor; never equate a successful workflow with a complete source window.
