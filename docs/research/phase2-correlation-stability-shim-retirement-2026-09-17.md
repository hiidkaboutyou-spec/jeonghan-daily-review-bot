# Lifecycle correlation compatibility-shim retirement evidence — 2026-09-17

This note records the evidence used to retire the historical `app.phase2_correlation_stability` path while leaving `app.lifecycle_correlation_runtime` unchanged as the canonical implementation.

## Scope

The retirement removes only the legacy module path and compatibility-only identity assertions. It does not change the stable translation-job identifier algorithm, first-event identifier preservation, retry behavior, lifecycle state semantics, persisted schemas, providers, Telegram delivery, schedules, secrets, or production dependencies.

The canonical import order remains:

`zero_silent_miss` → `phase2_runtime_compat` → `lifecycle_outcome_visibility_runtime` → `lifecycle_correlation_runtime` → Event Fusion.

## Migration baseline

PR #78 moved the implementation from `app.phase2_correlation_stability` to `app.lifecycle_correlation_runtime`, preserved the exact install position and `_phase2_correlation_stable` idempotency marker, and left a same-module-object compatibility shim.

The migration evidence measured approximately 93.5% execution coverage across 32 test contexts. The dedicated lifecycle-correlation regression verifies that the first event identifier and translation-job identifier remain stable when grouping labels change and the update later enters a retry stage.

Since PR #78, production package initialization has imported the canonical runtime directly.

## Fresh pre-removal evidence

PR #89's final Maintenance run `35241350984` generated artifact `10505881127` (SHA-256 `16fcafc1ca6907ba40e72e35692a96eba683c65210e5c11685a64b1ee4d6e523`) with per-test Coverage contexts enabled.

Its LibCST plan for `app.phase2_correlation_stability` → `app.lifecycle_correlation_runtime` reported:

- old module exists: `true`
- target already exists: `true`
- references: `3`
- structurally safe references: `1`
- manual-review references: `2`
- dynamic-string references: `2`
- import-order-sensitive references: `0`
- parse errors: `0`

All three references were compatibility evidence only:

1. `tests/test_phase2_correlation.py` imported the legacy path only to assert module identity;
2. the same test referenced `app.phase2_correlation_stability` in `sys.modules` only for that assertion;
3. `tests/test_module_migrations.py` referenced the legacy module name as migration-registry data.

There was no production Python importer. Grimp reported no direct importer, no downstream importer, and no application entrypoint chain for the shim.

The compatibility-only identity test is removed by the retirement change. The behavioral regression remains and continues to verify stable event/translation correlation across alternate group labels and retry state changes.

## Previous retirement proof before this change

The immediately preceding lifecycle-outcome retirement, PR #89, merged as `341dab172c2d5ca3d168c71e824383eb714bb48b` and completed its required independent production proof before this correlation retirement began:

- Jeonghan Daily Review Bot #4092 / run `35241697027`: state/DB restore, project validation, runtime smoke, live-provider checks, complete automatic monitor pass, DB checkpoint, encrypted backup creation/upload, production-outcome upload, state cache persistence, DB cache persistence, and fanfic queue all succeeded;
- Render Production Validation #269 / run `35241697140`: exact production image success;
- Hani Security Diagnostics / run `35241697000`: pip-audit and Bandit success;
- Hani CodeQL / run `35241697025`: success;
- Hani Maintenance Diagnostics / run `35241696973`: success.

This serial proof is intentional: each shim retirement must be independently attributable before the next one starts.

## Retirement change

The focused retirement:

1. deletes `app/phase2_correlation_stability.py`;
2. removes only the legacy-module identity assertion/import from `tests/test_phase2_correlation.py`;
3. leaves the stable-correlation behavioral regression unchanged;
4. marks the registry record `retired` and requires retired module files to remain absent while canonical modules still import;
5. removes only this retired path from recurring Maintenance LibCST plans.

## Required post-merge proof

Before the roadmap advances to degraded-X recovery retirement, the merge commit must again complete a real production run through state/database restore, project validation, runtime smoke, live-provider checks, full monitor pass, database checkpoint, encrypted backup/outcome upload, and state/database persistence. Security, CodeQL, Maintenance, fanfic/benchmark checks, and exact Render production-image validation must also be green.
