# Degraded X realtime checkpoint — 2026-10-07

## User-visible problem

The always-on Telegram assistant is healthy, but authenticated X is unavailable on
the webhook host. Public recovery is therefore intentionally non-authoritative.
Because `last_auto_run` correctly stays held, every scheduled degraded scan can
fall back to a very large lookback window. That protects eventual backfill, but it
also makes the near-real-time path do excessive repeated work and increases the
chance that bounded public timeline providers cannot cover the requested window.

## Production evidence

Railway production repeatedly showed degraded public recovery while keeping the
authenticated cursor held. A single source pass can reach every configured source,
but scheduled scans still remain partial by contract.

The older FxTwitter completeness experiment in PR #143 is especially useful
evidence. Its live shadow gate failed for 24 of 31 configured sources. Most failures
were `page_budget_exhausted`: several active sources exposed well over 100 rows in
the 24-hour test window, while the provider returned roughly 20 rows per page.
Increasing page budgets indefinitely would multiply external requests every
scheduled pass and still would not make a public provider authoritative.

## External research decision

- FxTwitter/FxEmbed remains useful as a read-only recovery provider. Current
  upstream work supports profile status pagination, replies, and `since` filtering,
  but public-provider success is not promoted to authenticated cursor authority.
- Official X timeline semantics still make authenticated APIs the stronger source
  for authoritative pagination and backfill.
- The solution therefore does **not** switch the success cursor to FxTwitter and
  does not add another state writer.

## Design

Add a second persisted timestamp, `last_degraded_scan_at`, with a deliberately
narrow authority:

- `last_auto_run` remains the only authenticated success/backfill cursor.
- `last_degraded_scan_at` is scheduling-only.
- It advances only when degraded recovery attempted every enabled configured source
  and recorded no source-level provider failure.
- After it exists, degraded scheduled scans use it with a two-hour overlap instead
  of repeatedly starting from the much older authenticated cursor.
- If any configured source fails, the degraded checkpoint does not advance and the
  existing failure path remains active.
- When an authenticated complete scan succeeds, the degraded checkpoint is cleared.

This keeps eventual backfill semantics while making the public recovery path much
closer to the user's real goal: fresh updates arriving continuously.

## Dashboard contract

The assistant must not describe this state as either “X complete” or a generic hard
failure. When the degraded scheduling checkpoint is active, the private dashboard
says public recovery is active and that the authoritative cursor is retained for
backfill.

## Verification required

1. Persist/reload the new state field.
2. A full public source pass advances only the degraded checkpoint.
3. The authenticated cursor remains unchanged.
4. A subsequent degraded pass uses the shorter realtime window with a two-hour
   overlap.
5. A source-level failure prevents checkpoint advancement and retains the failure
   path.
6. Dashboard never labels degraded public recovery as complete.
7. Full repository CI must pass on the exact PR head.
8. After merge, Railway must deploy the exact main SHA and a real degraded scan must
   log the realtime checkpoint path before production is called verified.

## Rollback

Revert this PR. The new state field is additive and ignored by older code, so no
state migration or destructive rollback is required.
