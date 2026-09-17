# Degraded-X recovery compatibility-shim retirement evidence — 2026-09-17

This note records the evidence used to retire `app.live_recovery_hardening` after the semantic runtime `app.x_degraded_recovery_runtime` had already become production-authoritative.

## Scope

The retirement removes only the historical import path and compatibility-only assertions. It does not change:

- authenticated X as full-success cursor authority;
- degraded-provider classification;
- syndication-first public recovery;
- bounded FxTwitter fallback or its page limit;
- actual degraded-batch attempt/failure reconciliation;
- production outcome classification;
- held-cursor recovery semantics;
- the `_hani_live_recovery_hardening` idempotency marker on the production application class;
- state/database schemas, Telegram delivery, schedules, secrets, providers, or production dependencies.

Canonical production installation remains in `app.sentry_runtime`, which imports `app.x_degraded_recovery_runtime` directly and installs it after production outcome hooks on the concrete `WebhookAwarePersonalAssistant` class.

## Serial prerequisite

The three lower-risk registered shims were retired and independently production-proven first:

1. channel source-fact normalization — PR #87;
2. lifecycle outcome visibility — PR #89;
3. lifecycle correlation — PR #90.

PR #90 merged as `9ce771edc4ad7bd0e3d457561008fe4a0468ec52`. Its post-merge production run #4101 completed state/private-review DB restore, project validation, runtime smoke, live-provider checks, one complete automatic monitor pass, DB checkpoint, encrypted recovery backup creation/upload, production-outcome upload, and state/DB cache persistence. Daily Watchdog #3198, Render #271, Security #78, CodeQL #69, and Maintenance #73 were green before this final retirement began.

## Fresh pre-removal evidence

Maintenance run `35244031585` on production `main` produced artifact `10506338030` (SHA-256 `3c9b2ed55b147e9ca59600fc6c4c17f83cbc8ef601d8a53e744bffe612a1d141`).

Its `stage-c-x-degraded-recovery-plan.md` reported:

- old module exists: true;
- canonical target exists: true;
- references: 3;
- structurally safe references: 1;
- manual-review references: 2;
- dynamic-string references: 2;
- import-order-sensitive references: 0;
- parse errors: 0.

All three references were compatibility evidence, not production callers:

- `tests/test_live_recovery_hardening.py` imported the legacy path only to assert legacy/canonical module identity and shared mutable flags;
- the same test referenced `app.live_recovery_hardening` in a `sys.modules` assertion;
- `tests/test_module_migrations.py` referenced the legacy path as migration-registry data.

Grimp evidence reported zero direct importers and zero downstream importers for the shim. `app.sentry_runtime` imports only `app.x_degraded_recovery_runtime`.

GitHub code search is not used as proof of absence when it reports incomplete results; the LibCST/Grimp evidence plus direct workflow/config/runtime inspection is authoritative for this retirement.

## Focused regression coverage retained

Removing compatibility assertions does not remove recovery behavior coverage. The canonical runtime remains directly tested for:

- incomplete collection + held cursor → `RECOVERY_REQUIRED`;
- reconciliation from the actual degraded source batch rather than inferred all-source success;
- FxTwitter fallback only after syndication failure, with the bounded `max_pages=3` contract;
- idempotent final scheduled-scan wrapping, original scan execution before reconciliation, and return-value preservation.

The original semantic migration PR #77 also passed full CI and a real production monitor run before this later retirement was considered.

## Retirement change

The focused change:

1. deletes `app/live_recovery_hardening.py`;
2. removes only legacy module-identity/shared-state assertions from the focused recovery test while retaining canonical behavior tests;
3. marks the migration `retired` in `config/module_migrations.json`;
4. requires all registered legacy paths to be absent while canonical paths remain importable;
5. removes the final recurring registered-shim LibCST plan from Maintenance and replaces it with a zero-active-shims registry assertion;
6. updates Stage C migration/closure documentation.

No third-party dependency or external refactor framework is added; the existing LibCST, Grimp, Coverage.py, Ruff, security diagnostics, and project-native tests provide the needed evidence.

## Required final proof

Before merge, require project validation, full Maintenance unittest/Coverage, Security, CodeQL, fanfic/translation checks, and exact Render production-image validation.

After merge, require a real zero-shim `main` production run through restore, runtime smoke, live providers, a complete monitor pass, DB checkpoint, encrypted backup/outcome upload, and state/DB persistence. Only after that proof is green is the registered compatibility-shim subphase considered production-complete; the next action is the closure audit of remaining historical implementation modules, not an automatic rename sweep.
