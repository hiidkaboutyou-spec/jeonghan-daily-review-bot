# Lifecycle outcome visibility compatibility-shim retirement evidence — 2026-09-17

This note records the evidence used to retire the historical `app.phase2_final_visibility` path while leaving `app.lifecycle_outcome_visibility_runtime` unchanged as the canonical implementation.

## Scope

The retirement removes only the legacy module path and compatibility-only test assertions. It does not change partial-media detection, fidelity-rejection classification, observability events, Telegram/media return values, state/database schemas, providers, schedules, secrets, or production dependencies.

The canonical import order remains:

`zero_silent_miss` → `phase2_runtime_compat` → `lifecycle_outcome_visibility_runtime` → `lifecycle_correlation_runtime` → Event Fusion.

The package-local name `_phase2_final_visibility` in `app/__init__.py` still points directly to the canonical module. It does not import or recreate the retired legacy module path and is intentionally left untouched in this focused removal.

## Migration baseline

PR #79 moved the implementation from `app.phase2_final_visibility` to `app.lifecycle_outcome_visibility_runtime`, preserved the exact install position and idempotency markers, and left a same-module-object compatibility shim. Its planning-first evidence found one production structural reference before migration and no dynamic production caller. Stage B measured about 84.5% execution coverage across 13 test contexts, and focused outcome tests covered both established behaviors.

Since PR #79, production package initialization has imported the canonical module directly.

## Fresh pre-removal evidence

The final Maintenance artifact from PR #87 (`10504433483`, SHA-256 `64153890ad1cd376c8b1178cae8d3602ed24dd91606124d364220c4be04d909e`) was generated against the post-watchdog-cleanup Stage C state with per-test Coverage contexts enabled.

Its LibCST plan for `app.phase2_final_visibility` → `app.lifecycle_outcome_visibility_runtime` reported:

- old module exists: `true`
- target already exists: `true`
- references: `3`
- structurally safe references: `1`
- manual-review references: `2`
- dynamic-string references: `2`
- import-order-sensitive references: `0`
- parse errors: `0`

The three references were all compatibility-test evidence:

1. `tests/test_phase2_outcomes.py` imported the legacy path only to assert module identity;
2. the same test referenced `app.phase2_final_visibility` in `sys.modules` only for that compatibility assertion;
3. `tests/test_module_migrations.py` referenced the legacy module name as registry data.

There was no production Python importer. Grimp reported no direct importer, no downstream importer, and no application entrypoint chain for the shim.

The compatibility-only test assertion is removed by the retirement change. The two behavioral regressions remain unchanged and continue to verify:

- partial multi-asset delivery records `media_status="partial_failed"` and `media_partial_failure` without changing successful-send behavior;
- manual-review drafts record `translation_status="fidelity_rejected"` / `manual_review_required` without leaking private caption content.

## Production proof before removal

PR #87's merge commit `78332d335a166a1dbaf9dbd97cf3a2e07710d64f` completed all required post-merge gates while production was already using the canonical outcome runtime:

- Jeonghan Daily Review Bot #4081 / run `35239294992`: full state/DB restore, project validation, runtime smoke, live providers, complete monitor pass, DB checkpoint, encrypted backup/upload, production outcome, state/DB persistence, and fanfic queue succeeded;
- Daily Watchdog #3178 / run `35239566868`: success;
- Render Production Validation #266 / run `35239294966`: exact image success;
- Security #73, CodeQL #64, Maintenance #68, and Fanfic #878: success.

## Retirement change

The focused retirement:

1. deletes `app/phase2_final_visibility.py`;
2. removes only the legacy-module identity assertion/import from `tests/test_phase2_outcomes.py`;
3. keeps the canonical behavioral outcome tests unchanged;
4. marks the registry record `retired` and requires retired module files to remain absent while canonical modules import;
5. removes only this retired path from recurring Maintenance LibCST plans.

## Required post-merge proof

After merge, `main` must again complete a real production run through restore, validation, runtime smoke, live providers, full monitor pass, checkpoint, encrypted backup/outcome upload, and state/database persistence. The retirement is not considered complete until those gates are green.
