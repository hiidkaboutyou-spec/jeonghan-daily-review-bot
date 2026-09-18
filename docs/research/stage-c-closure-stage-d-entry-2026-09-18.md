# Stage C closure and Stage D entry — 2026-09-18

## Closure decision

Stage C's rename-oriented compatibility-migration queue is closed.

PR #112 retired the final registered compatibility path, `app.phase3_recovery_hardening`, and merged to `main` as `aa5cf158f84876557546af001985a6cca42e5943`.

Independent real-main evidence passed:
- Jeonghan Daily Review Bot #4221
- Hani Maintenance Diagnostics #138
- Render Production Validation #315
- Hani Security Diagnostics #146
- Hani CodeQL #107
- Nightly Jeonghan Fanfic Digest #987
- Jeonghan Daily Watchdog #3324 and #3325

Daily #4221 passed runtime smoke, live-provider checks, one complete automatic monitor pass, private-review database checkpoint, production outcome upload, bot-state persistence, and private-review database persistence.

Maintenance #138 artifact `10559581027` has digest `sha256:1ad11a23c8145481cf16d329d22d94b8fe16019104607e47f1fbc54bd7e0e4b3`.

## Fresh inventory

The post-retirement inventory leaves exactly four historical-name modules:
- `app.channel_part4_benchmark_hook`
- `app.channel_part4_hardening`
- `app.phase2_runtime_compat`
- `app.source_authority_hardening`

They remain runtime-linked/high-risk and are retained by design. A historical-looking filename is not a reason to reopen Stage C.

Every entry in `config/module_migrations.json` is retired. Maintenance must now fail if any active compatibility shim appears without a separately researched and documented migration.

## Stage C workflow debt removed

The recurring Maintenance workflow must no longer:
- generate a LibCST plan for the retired `app.phase3_recovery_hardening` path;
- describe that migration as the active Stage C target;
- allow one active compatibility shim.

LibCST remains installed as a maintenance planning tool for future explicitly approved structural migrations, but no retired migration receives a recurring plan.

## Stage D tool research

### Import Linter 2.15

Preferred first pilot.

Reasons:
- actively maintained release/CI;
- BSD-2-Clause;
- builds on Grimp, which Hani already uses for import evidence;
- supports narrow forbidden/protected contracts plus layers, independence, and acyclic-siblings contracts;
- can remain maintenance/CI-only with zero production runtime dependency impact.

Pilot policy:
1. one narrow contract that current code already satisfies;
2. report-only first;
3. inspect dynamic imports and import-time patching separately;
4. do not use broad wildcard ignores to force a contract green;
5. do not promote to blocking in the same change that introduces the contract;
6. only expand after current code and tests demonstrate a stable intended boundary.

### Tach

Deferred alternative.

Current reviewed latest release is v0.35.0. Tach can enforce dependencies, interfaces, and cycles, but it introduces a separate architecture model/toolchain. Do not install Tach in parallel with the Import Linter pilot.

## Stage D entry gate

Before adding an architecture contract:
- Stage C closeout maintenance PR must be green and merged;
- zero active compatibility shims must remain;
- the selected boundary must be proven by current imports/Grimp evidence;
- the pilot must not change runtime code, persisted state, provider behavior, Telegram/AO3 behavior, schedules, secrets, or production dependencies.

## Protected invariants

Preserve:
- `x_retrieval_checkpoints`;
- CHECKPOINT_VERSION and checkpoint identity/normalization;
- retry/fallback accounting;
- source authorization;
- cursor advancement semantics;
- import-time recovery patch ordering;
- provider behavior;
- Telegram/AO3 delivery;
- schedules and secrets.
