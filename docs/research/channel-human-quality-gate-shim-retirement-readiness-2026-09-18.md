# Channel human-quality-gate shim retirement readiness — 2026-09-18

## Decision

`app.channel_part4_humanfix` is now eligible for a later focused retirement change, but this evidence-only step does not delete the shim. The canonical implementation remains `app.channel_human_quality_gate_runtime`.

## Production proof

The semantic migration landed on `main` as commit `203c4c53395ee7971186b95e85db5a6bde455efd` (PR #98). Fresh post-merge GitHub Actions evidence for that exact SHA is green:

- `Jeonghan Daily Review Bot` run 4161 / run id `35284267353`: completed, success.
- `Render Production Validation` run 286 / run id `35284267303`: completed, success.
- `Hani Maintenance Diagnostics` run 95 / run id `35284267248`: completed, success.
- `Hani CodeQL` run 84 / run id `35284267281`: completed, success.
- `Nightly Jeonghan Fanfic Digest` run 937 / run id `35284267285`: completed, success.
- `Jeonghan Daily Watchdog` run 3263 / run id `35290911248`: completed, success after the main run.

This satisfies the requirement that the semantic runtime be production-proven before a separate shim-removal change is attempted.

## Fresh LibCST evidence

The maintenance artifact `hani-maintenance-reports` from run `35284267248` contains `stage-c-channel-human-quality-gate-plan.md` for the exact merged SHA. It reports:

- old module exists: true;
- canonical target exists: true;
- references: 2;
- structurally safe references: 0;
- manual-review references: 2;
- dynamic string references: 2;
- import-order-sensitive references: 0;
- parse errors: 0.

The only references are registry-contract tests in `tests/test_module_migrations.py` at the then-current lines 77 and 104. They are expected migration-state assertions, not production/workflow/CLI/config/benchmark callers. No automatic LibCST rewrite is authorized.

## Required focused retirement change

The next focused PR may retire the shim only if it performs all of the following atomically:

1. delete `app/channel_part4_humanfix.py`;
2. change the registry entry for `app.channel_part4_humanfix` to `retired`, add `retired_on: 2026-09-18`, and point `retirement_record` at the final retirement record;
3. update `tests/test_module_migrations.py` so the human-quality migration is asserted as retired and the active-shim expectation becomes empty;
4. remove the now-obsolete always-generated human-quality migration-plan step/artifact from `.github/workflows/maintenance-diagnostics.yml` while retaining the generic registry guard;
5. preserve runtime import order: `channel_part4_hardening`, `channel_source_fact_normalization_runtime`, `channel_human_quality_gate_runtime`, `channel_part4_qualityfix`, `channel_part4_benchmark_hook`;
6. preserve the single canonical mutable state for `HUMAN_GATE_VERSION`, `HUMAN_GATE_FINGERPRINT`, and `verify_hard_facts`;
7. run focused human-quality-gate, quality-repair, benchmark-freshness and migration-registry tests plus the full project validation suite;
8. require CI, Security/CodeQL, maintenance diagnostics, Render production validation, and a complete production main run to pass before merge/closure.

## Next Stage C target

Do not migrate `app.channel_part4_qualityfix` in the same retirement PR. Once the human-quality shim is retired and production-proven, start a separate planning-first evidence pass for `channel_part4_qualityfix`, following the repository roadmap and one-active-migration-at-a-time rule.

## Dependency decision

No new GitHub repository or dependency is justified for this retirement gate. Existing LibCST planning, Grimp evidence, Coverage.py contexts, Ruff, Vulture, Complexipy, Deptry, CodeQL, Render validation, and production workflows already cover the decision surface. Adding another tool here would increase supply-chain and maintenance risk without closing an uncovered gate.
