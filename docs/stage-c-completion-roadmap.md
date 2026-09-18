# Stage C completion roadmap

Stage C is the evidence-first structural cleanup phase for Daily Hani. This roadmap closes the registered compatibility-shim subphase without turning cleanup into a broad rewrite.

## Registered migrations

- `app.channel_part4_finalfix` → `app.channel_source_fact_normalization_runtime` — **retired and production-proven in PR #87**.
- `app.phase2_final_visibility` → `app.lifecycle_outcome_visibility_runtime` — **retired and production-proven in PR #89**.
- `app.phase2_correlation_stability` → `app.lifecycle_correlation_runtime` — **retired and production-proven in PR #90**.
- `app.live_recovery_hardening` → `app.x_degraded_recovery_runtime` — **final registered retirement step**; removal is permitted only because #90 completed its own production proof and the fresh degraded-X audit found only compatibility references.

Each retirement is a separate focused change. Independent shim removals are never combined.

## Required retirement gate

1. Start from current green `main` and verify the previous retirement's post-merge production run.
2. Inspect the latest Maintenance LibCST plan and Grimp evidence for the exact legacy module.
3. Inspect every structural/dynamic reference manually; compatibility-only assertions may be removed only while canonical behavior tests remain.
4. Keep the canonical implementation and import/install order unchanged.
5. Delete only the legacy shim and compatibility-only assertions; mark the registry record `retired` and preserve a retirement evidence note.
6. Stop recurring CI planning for the retired path.
7. Require full PR validation: unittest/Coverage Maintenance, Security, CodeQL, benchmark/fanfic checks, and exact Render production image.
8. After merge, require a real `main` run through state/DB restore, project validation, runtime smoke, live providers, complete monitor pass, DB checkpoint, encrypted backup/outcome upload, and state/DB persistence.

## Final degraded-X retirement evidence

The final active shim is `app.live_recovery_hardening`. Production already imports only `app.x_degraded_recovery_runtime` from `app.sentry_runtime`. On `main` commit `9ce771edc4ad7bd0e3d457561008fe4a0468ec52`, Maintenance run `35244031585` generated a fresh plan with three references: one structural compatibility-test import plus two dynamic compatibility/registry strings. There were zero production importers, zero import-order-sensitive legacy references, and zero parse errors. Grimp showed no direct or downstream importer for the shim.

PR #90 independently completed post-merge production proof before this final retirement began: main #4101 completed restore, validation, smoke, live providers, full monitor pass, checkpoint, encrypted backup/outcome upload, and state/DB persistence; Daily Watchdog #3198, Render #271, Security #78, CodeQL #69, and Maintenance #73 were also green.

The canonical recovery behavior remains covered after compatibility assertions are removed: recovery-required classification, actual degraded-batch reconciliation, syndication→FxTwitter fallback ordering, and idempotent final-scan wrapping remain direct tests of `app.x_degraded_recovery_runtime`.

## Closure audit after all registered shims are retired

Once the final retirement is merged and its zero-shim production run is green, run a fresh PR/manual Maintenance pass with per-test Coverage contexts and inspect the remaining historical-name implementation modules. Do not rename them merely to make filenames prettier. For each remaining module choose exactly one outcome:

- **migrate later:** a semantic boundary is clear, focused coverage is adequate, and the maintenance benefit justifies a staged compatibility migration;
- **retain by design:** the current name/path is an intentional production contract or a rename would add risk without useful architectural value.

Record those decisions in `docs/repository-maintenance.md`. The registered-shim subphase is complete when `config/module_migrations.json` has no `compatibility-shim` records, Maintenance generates no plans for retired migrations, and the final zero-shim production validation is green.

Stage D architecture enforcement remains deferred until the closure audit establishes stable intended boundaries.


## Post-closure migration sequence

The registered-shim closure audit completed in PR #95 and classified the remaining historical implementation modules. PR #97 then completed the focused coverage precursor for `app.channel_part4_humanfix`, measuring 98.44% execution coverage across 133 test contexts without changing production behavior.

The `app.channel_part4_humanfix` → `app.channel_human_quality_gate_runtime` semantic migration completed in PR #98 and received independent production proof. PR #99 recorded the final retirement-readiness evidence. The historical compatibility shim is now retired in a separate focused change after the final LibCST audit found only migration-registry contract strings and no runtime caller.

Fresh planning for `app.channel_part4_qualityfix` completed in PR #101, PR #102 production-proved the benchmark-freshness precursor, PR #103 completed the semantic migration, and PR #104 retired the historical shim with independent production proof. PR #105 completed the X-recovery coverage precursor (base 76.6% / 129 contexts; integrity 99.2% / 33 contexts). PR #106 generated fresh dual LibCST plans. The base candidate had 56 references (11 structural, 45 dynamic/manual, one order-sensitive, zero parse errors) and three direct importers; the integrity candidate had six references (two structural, four dynamic/manual, one order-sensitive, zero parse errors). The active Stage C move is now `app.phase3_recovery` → `app.x_resumable_recovery_runtime`: the base implementation is unchanged, the old path is a same-module-object shim, all audited production/test callers bind to the canonical path, and focused Maintenance coverage follows the canonical implementation. Persisted key `x_retrieval_checkpoints` and all checkpoint/cursor semantics are unchanged. Require full CI plus independent real-main production proof and a fresh retirement audit before removing the base shim; only after that may `app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime` begin. Detailed migration evidence: `docs/research/x-resumable-recovery-semantic-migration-2026-09-18.md`.

## Base X-recovery shim retired

The base migration is independently production-proven and its historical shim is
now retired.

Final audit evidence:
- LibCST: 2 references, both migration-test strings; 0 structural; 0 order-sensitive;
  0 parse errors.
- Grimp: 0 direct importers; 0 downstream importers.
- Daily #4205 and the full post-merge validation set are green.

The next Stage C gate is **planning first** for
`app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime`.
No integrity implementation move belongs in the base-shim retirement change.

## Active X-recovery integrity semantic migration

Base recovery is canonical, its historical shim is retired, and the next semantic
boundary is now active:

`app.phase3_recovery_hardening` → `app.x_recovery_integrity_runtime`.

Fresh evidence from Maintenance #127: 6 references total, 2 structural, 4
synthetic/manual fixture strings, 1 order-sensitive import, 0 parse errors,
99.2% coverage across 33 contexts, and only `app` as a runtime importer.

The implementation remains unchanged under a same-module-object compatibility shim.
The next sequence is strict:

`integrity migration` → `real-main production proof` → `fresh retirement audit`
→ `integrity shim retirement`.

Do not combine migration and retirement.

