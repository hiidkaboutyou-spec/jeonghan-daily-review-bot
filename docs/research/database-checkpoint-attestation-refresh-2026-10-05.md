# Database checkpoint attestation refresh — 2026-10-05

## Initial state

Canonical base: `main@a71704a8463ef3084a5d1a57115a9929257f579c`.

Issue #136 remained valid on current main. The application finalized and wrote
`production-outcome.json` before the workflow-level SQLite integrity/checkpoint
step. The workflow then uploaded the old artifact unchanged.

The older Draft PR #141 is stale and is not merged or rebased here.

## Observed -> evidence -> root cause -> impact

**Observed:** `state.database_checkpoint_success=false` in an outcome artifact
did not tell an operator/watchdog whether the database checkpoint had never been
attempted or had actually failed.

**Evidence:**
- `OutcomeBuilder.mark_database_checkpoint` existed, but current runtime did not
  call it after the workflow checkpoint.
- `.github/workflows/main.yml` ran `PRAGMA quick_check` and
  `wal_checkpoint(TRUNCATE)` after the application had already emitted the
  outcome.
- outcome upload occurred after the checkpoint but uploaded the pre-checkpoint
  contents.

**Root cause:** checkpoint authority lived in GitHub Actions while outcome
serialization authority lived in the application, with no post-checkpoint bridge.

**User impact:** state/recovery evidence was ambiguous. A watchdog or maintainer
could not distinguish "not attested" from a real database checkpoint failure.

## Research / Build-vs-borrow

No external dependency is justified. SQLite exposes the required integrity and
WAL checkpoint primitives through Python stdlib `sqlite3`; the repository
already owns outcome parsing/classification/serialization.

Decision: **ADAPT** the prior design recorded in #136/#141, but reimplement on
current main with the repository's actual `unittest` gate and current workflow.

Rejected:
- merging stale #141: stale base and old pytest-only tests;
- adding a database/checkpoint library: no missing primitive;
- treating `database_checkpoint_success=false` as failure unconditionally:
  breaks legacy artifacts where the checkpoint was simply not attested.

## Implementation

1. `StateOutcome` now has `database_checkpoint_attempted`, default false.
2. `mark_database_checkpoint` sets attempted=true before success state.
3. Classification is FAILED with stable reason
   `database_checkpoint_failed` only when attempted=true and success=false.
4. `app.database_checkpoint_attestation`:
   - loads the existing redacted outcome;
   - persists attempted=true/success=false **before** opening SQLite;
   - opens the existing DB in `mode=rw` so a typo cannot create a new DB;
   - requires exact `PRAGMA quick_check == ok`;
   - requires a non-busy, fully checkpointed `wal_checkpoint(TRUNCATE)`;
   - persists success=true only after both checks;
   - emits bounded error text.
5. Daily workflow calls the helper in the existing checkpoint position.
6. The existing outcome upload remains afterward, including on failure.

No source collection, cursor, Telegram, provider, schedule, database schema,
secret, or private-row logging behavior changes.

## Regression matrix

Credential-free `unittest` coverage:
- real SQLite WAL success -> attempted=true/success=true;
- missing DB -> persisted attempted failure;
- quick_check corruption result -> persisted failure and closed connection;
- busy and partial WAL checkpoint -> persisted failure;
- legacy schema-v1 artifact without attempted field -> readable and not falsely failed;
- CLI failure text is bounded and does not expose a temporary DB path;
- workflow ordering: attestation command occurs before outcome upload and the old
  inline checkpoint implementation is absent.

## Before -> after

Before:
- checkpoint may run;
- artifact still says success=false;
- false means either "not attempted" or "failed".

After:
- not attempted -> attempted=false, success=false (legacy-compatible);
- checkpoint failure/crash after prewrite -> attempted=true, success=false, status=failed;
- checkpoint success -> attempted=true, success=true.

## Risk / rollback

Primary risk is stricter failure semantics: a busy/partial checkpoint now makes
the workflow fail instead of permitting a green run with ambiguous durability.
That is intentional for state correctness.

Rollback is a focused revert of this PR. There is no DB migration or persisted
application-state migration; the added outcome field is backward compatible.

## Acceptance

Do not merge until exact-head CI is green. After merge, issue #136 still requires
one real-main live Daily artifact proving attempted=true/success=true and an
independent Watchdog observation before closure.
