# Stage C completion roadmap

Stage C is the evidence-first structural cleanup phase for Daily Hani. This roadmap closes the currently registered compatibility-shim subphase without turning cleanup into a broad rewrite.

## Scope

The immediate completion target is the four registered app migrations in `config/module_migrations.json`.

- `app.channel_part4_finalfix` → `app.channel_source_fact_normalization_runtime` — retired in PR #87.
- `app.phase2_final_visibility` → `app.lifecycle_outcome_visibility_runtime` — next retirement candidate.
- `app.phase2_correlation_stability` → `app.lifecycle_correlation_runtime` — retire after outcome visibility proves stable without its shim.
- `app.live_recovery_hardening` → `app.x_degraded_recovery_runtime` — retire last because it owns mutable install/classification state and the degraded-X production recovery path.

Each retirement remains a separate focused PR. Never combine the three remaining removals into one change.

## Required gate for every retirement

1. Start from current green `main` and verify the previous retirement's post-merge production run.
2. Inspect the latest Maintenance LibCST plan and Grimp evidence for that exact legacy module.
3. Inspect every structural/dynamic reference manually. Compatibility-only test references may be removed only when the canonical behavior tests remain.
4. Keep the canonical implementation and its import/install order unchanged.
5. Delete only the legacy shim and compatibility-only assertions; mark the registry record `retired` and preserve a retirement evidence note.
6. Stop recurring CI planning only for the retired path; keep plans for active shims.
7. Require full PR validation: unittest/Coverage Maintenance, Security, CodeQL, benchmark/fanfic checks, and exact Render production image.
8. After merge, require a real `main` run through state/DB restore, project validation, runtime smoke, live providers, complete monitor pass, DB checkpoint, encrypted backup/outcome upload, and state/DB persistence. When relevant, verify the workflow-triggered Daily watchdog too.

## Order

### A. Lifecycle outcome visibility

Retire `app.phase2_final_visibility` first. The latest plan has no production importers; its remaining structural/dynamic references are compatibility-test evidence. Preserve the canonical runtime between `phase2_runtime_compat` and `lifecycle_correlation_runtime`.

### B. Lifecycle correlation

Retire `app.phase2_correlation_stability` second. Preserve the canonical correlation runtime in the same import-order position and retain the stable event/translation-job identity behavior.

### C. Degraded X recovery

Retire `app.live_recovery_hardening` last. This is the highest-sensitivity registered shim because the canonical module owns mutable install flags and wraps the final scheduled scan. Remove only after the first two retirements have independently passed production.

## Closure audit after all registered shims are retired

Run a fresh PR/manual Maintenance pass with per-test Coverage contexts and inspect the remaining historical-name implementation modules. Do not rename them merely to make filenames prettier. For each remaining module, choose exactly one outcome:

- **migrate later:** a semantic boundary is clear, focused coverage is adequate, and the maintenance benefit justifies a staged compatibility migration;
- **retain by design:** the current name/path is still the production contract or a rename would add risk without useful architectural value.

Record that decision in `docs/repository-maintenance.md`. Stage C's registered-shim subphase is complete when `config/module_migrations.json` has no `compatibility-shim` records, Maintenance generates no plans for retired migrations, and the final production validation is green.

Stage D architecture enforcement remains deferred until the closure audit establishes stable intended boundaries.
