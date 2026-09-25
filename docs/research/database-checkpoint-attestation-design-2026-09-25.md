# Database checkpoint attestation design — 2026-09-25

## Verified frontier

Canonical base: `main@26f84d96474019e75297ce95780d1a7ca5a90761`.

On this exact SHA, scheduled Daily run 36190077105 succeeded and independent Watchdog run 36190287374 succeeded. Nightly Fanfic push validation 36186188815 also succeeded.

The authenticated X runner matrix has separately ruled out a simple hosted-runner OS migration: Ubuntu, macOS, and Windows all observed the same redacted HTTP 403 result. That provider issue is independent of this checkpoint contract.

## Gap

Issue #136 remains valid. The application writes `production-outcome.json` before the workflow-level SQLite checkpoint. The workflow then runs `PRAGMA quick_check` and `PRAGMA wal_checkpoint(TRUNCATE)`, but the already-written outcome is uploaded afterward without an authoritative record that the database checkpoint was attempted and succeeded.

The existing boolean `database_checkpoint_success=false` is ambiguous: it currently means “not attested by the application artifact”, not necessarily “the workflow attempted a checkpoint and it failed”.

## Required contract

Implement the checkpoint attestation as one atomic focused change:

1. Add `database_checkpoint_attempted` to `StateOutcome`, defaulting to false for backward compatibility.
2. Keep `database_checkpoint_success` but interpret it only when `database_checkpoint_attempted=true`.
3. An attempted checkpoint that does not succeed must classify the final production outcome as `failed` with a stable reason such as `database_checkpoint_failed`.
4. Replace the inline workflow checkpoint body with a repository-owned helper that:
   - loads the existing redacted outcome;
   - marks checkpoint attempted;
   - runs SQLite `quick_check`;
   - runs `wal_checkpoint(TRUNCATE)`;
   - marks success only after both operations finish;
   - reclassifies and persists the outcome;
   - on failure, persists attempted=true/success=false before returning non-zero.
5. The existing always-run outcome artifact upload must remain after the checkpoint step, so failed attestation evidence is still available to the watchdog and operators.
6. Do not alter source collection, cursor advancement, Telegram delivery, provider selection, schedules, state format, or database schema.

## Validation matrix

Blocking isolated tests should cover:

- successful checkpoint writes attempted=true/success=true;
- quick_check failure writes attempted=true/success=false and exits non-zero;
- WAL checkpoint failure behaves the same way;
- malformed or missing outcome fails closed;
- legacy schema-v1 artifacts without the attempted field remain readable and mean “not attempted”, not “failed”;
- reclassification gives database checkpoint failure precedence over healthy/degraded/recovery-required application outcomes;
- emitted failure details remain bounded and sanitized;
- no live Telegram or provider calls occur in tests.

Then require exact-head normal CI, followed after merge by one real-main due Daily run and an independent Watchdog run. Close issue #136 only after the uploaded production outcome proves the checkpoint was attempted and successful on real main.

## Rollback

Revert the focused attestation change. No database migration or cursor repair is required because the design only adds outcome metadata and workflow-side attestation.

## Dependency decision

No new dependency is justified. Python stdlib `sqlite3`, existing production-outcome parsing/serialization, and the current workflow are sufficient.
