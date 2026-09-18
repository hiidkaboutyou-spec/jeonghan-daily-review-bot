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

Fresh planning for `app.channel_part4_qualityfix` completed in PR #101: 99.1% coverage across 129 test contexts, one direct runtime importer, and four structural LibCST references with zero dynamic/manual references and only `app/__init__.py` order-sensitive. PR #102 then bound the quality-repair implementation into the human benchmark production fingerprint and received independent main proof on `247374faf284c8ccf704b520819140408013ec3b`. The current Stage C migration is now `app.channel_part4_qualityfix` → `app.channel_quality_repair_runtime`, with an unchanged canonical implementation plus a same-module-object compatibility shim. Preserve exact install order and the canonical human-gate mutable state; do not retire the shim until a later focused post-production-proof change.
