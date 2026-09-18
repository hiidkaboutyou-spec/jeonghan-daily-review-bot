# X recovery coverage precursor — 2026-09-18

## Decision

The coverage prerequisite for the Stage C X recovery family is complete enough to proceed to a separate planning-first migration analysis.

This precursor changes tests and Maintenance evidence only. It does not rename, move, delete, or change the runtime implementation of:

- `app.phase3_recovery`
- `app.phase3_recovery_hardening`

The next step is **not** a semantic move yet. Generate and inspect fresh LibCST plans for both recovery modules together, then design the base/integrity sequence before authorizing either migration.

## Production baseline

The precursor starts from production-proven main:

`34de4047701f51edb9ca5d2013535aac82e3933f`

That commit completed the quality-repair compatibility retirement and passed the real production Bot/Render/Security/CodeQL/Maintenance/Fanfic/Watchdog gate.

## Closure-audit baseline

PR #95 measured:

| Module | Coverage | Test contexts |
| --- | ---: | ---: |
| `app.phase3_recovery` | 66.2% | 116 |
| `app.phase3_recovery_hardening` | 80.8% | 26 |

The closure audit required direct coverage of checkpoint sanitization, persisted-state migration, retry/failure behavior and recovery-integrity branches before any semantic migration.

## First exact missing-line audit

PR #105 added a Maintenance-only focused `coverage report --show-missing` artifact for the two recovery modules.

Maintenance #107 / run `35336296861` artifact:
- id: `10543023474`
- digest: `sha256:775f7823993435854370d5565f2dc53cba2c16c6f84e9a1c9bd66b5c3619784d`

The first report confirmed that test-count breadth was hiding important direct branch gaps:
- base recovery: 142 unexecuted statements;
- integrity hardening: 15 unexecuted statements.

The relevant uncovered responsibilities included checkpoint validation/normalization/pruning, exact/older checkpoint selection, retry/failure handling, fallback bounds, and scoped identity recovery.

## Focused precursor coverage

`tests/test_phase3_recovery_coverage_precursor.py` now directly protects active contracts including:

- malformed/corrupt checkpoint rejection;
- counter clamping and cursor validation;
- malformed persisted checkpoint container normalization;
- checkpoint prune/save/clear failure behavior;
- exact versus compatible older checkpoint selection;
- malformed resumed-checkpoint conservative reset;
- requested-window narrowing resetting an incompatible wider checkpoint;
- checkpoint helper fail-closed behavior without state;
- bounded retry-delay endpoints;
- invalid/unconfigured source rejection;
- legacy fallback only when raw provider support is unavailable;
- conversion exceptions preserving a resumable checkpoint and becoming `XCollectionError`;
- source-authorized partial recovery from a failed core collection;
- successful collection merging only typed partial updates and restoring transient flags;
- bounded syndication fallback budget exhaustion;
- integrity profile recovery after retry;
- first-attempt profile success without unnecessary retry/search;
- scoped-search failure and no-search exhaustion;
- malformed checkpoint-update parsing in the integrity sanitizer.

No production implementation file changed.

## Important layering discovery

Gross line coverage of `app.phase3_recovery.py` is not an accurate measure of active production-contract coverage.

Current startup deliberately replaces three historical implementation bodies from the base module:

1. `phase3_recovery_hardening` replaces `phase3_recovery._sanitize_checkpoint` with the non-expiring, lossless integrity sanitizer.
2. `phase3_recovery_hardening` replaces `phase3_recovery._lookup_user` with scoped identity recovery.
3. `completeness_provider_proof` replaces `phase3_recovery._provider_page` with the structural provider-proof implementation.

The remaining large missing-line blocks in the base module are therefore dominated by superseded implementation bodies. Executing those old bodies only to inflate a percentage would test a startup state that production intentionally replaces.

This was proven operationally during the precursor: an initial direct provider-page test failed because package startup correctly installed `completeness_provider_proof._provider_page`. The test was removed rather than bypassing the production install layer.

## Final evidence

Final PR-head Maintenance #111 / run `35337093412` artifact:
- id: `10543720374`
- digest: `sha256:7cc09495958c7d12ddb59deb56421e8eeb2cd997c4fe79db86c0d5d430e30cff`

Final measured evidence:

| Module | Coverage | Test contexts |
| --- | ---: | ---: |
| `app.phase3_recovery` | **76.6%** | **129** |
| `app.phase3_recovery_hardening` | **99.2%** | **33** |

Focused report rounds the same values to 77% and 99%.

The final base missing-line report is concentrated in the three superseded implementation regions plus a small set of non-critical/install-idempotency edges. The active checkpoint/state/retry/fallback contracts required by the closure audit now have direct regression tests.

Deptry on the final head reports:

`Success! No dependency issues found.`

## Tooling/dependency decision

No new GitHub project or dependency is justified for this precursor.

Coverage.py, Grimp, the native module-family inventory, existing unittest suite, Ruff, Deptry and later LibCST planning already expose the relevant failure surface. Adding another coverage/refactor framework would expand maintenance and supply-chain risk without closing an evidence gap.

## Next Stage C gate

Do not rename either recovery module in this precursor.

The next separate planning-first pass must generate fresh LibCST/reference/import-order evidence for **both** proposed semantic boundaries:

- `app.phase3_recovery` → candidate `app.x_resumable_recovery_runtime`
- `app.phase3_recovery_hardening` → candidate `app.x_recovery_integrity_runtime`

The plan must inspect:
- every structural and dynamic/manual reference;
- `app/__init__.py` install order;
- the fact that the integrity layer patches names on the base recovery module at import time;
- StateStore checkpoint schema/persisted key compatibility;
- interaction with `completeness_provider_proof`, source authority, degraded-provider recovery and scheduled cursor semantics;
- whether the base migration must land and become production-proven before the integrity module is moved.

Design the two-module sequence together, but keep later implementation migrations as focused, independently validated changes.
