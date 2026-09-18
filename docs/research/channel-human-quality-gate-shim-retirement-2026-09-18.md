# Channel human-quality-gate shim retirement — 2026-09-18

## Decision

The historical compatibility path `app.channel_part4_humanfix` is retired. The only supported implementation path is now `app.channel_human_quality_gate_runtime`.

This is a focused removal step. It does not change human-polish logic, source-fidelity rules, writer behavior, provider behavior, Telegram delivery, persisted state/schema, schedules, secrets, or production dependencies.

## Preconditions satisfied

The semantic migration landed in PR #98 as `203c4c53395ee7971186b95e85db5a6bde455efd`.

Independent post-merge production proof for that exact semantic runtime completed successfully:
- Jeonghan Daily Review Bot run 4161 / `35284267353`;
- Render Production Validation run 286 / `35284267303`;
- Hani Maintenance Diagnostics run 95 / `35284267248`;
- Hani CodeQL run 84 / `35284267281`;
- Nightly Jeonghan Fanfic Digest run 937 / `35284267285`;
- workflow-triggered Jeonghan Daily Watchdog run `35290911248`.

PR #99 then recorded the retirement-readiness evidence and merged as `1d579775f955b2b861db428f2f4a7e59888ea8ef`.

The final pre-removal LibCST evidence reported exactly two remaining legacy references, both migration-registry contract strings in `tests/test_module_migrations.py`; it reported zero structural references, zero import-order-sensitive references, and zero parse errors. No production, workflow, CLI, config, benchmark, or dynamic-import caller remained.

## Removal scope

This retirement change:
1. deletes `app/channel_part4_humanfix.py`;
2. marks the migration record retired with this document as the durable retirement record;
3. changes migration tests to require zero active compatibility shims and require the retired legacy path to be absent;
4. removes the obsolete recurring human-quality migration-plan step/artifacts from Maintenance CI;
5. keeps `app.channel_human_quality_gate_runtime` and all canonical callers unchanged;
6. preserves the runtime order `channel_part4_hardening` → `channel_source_fact_normalization_runtime` → `channel_human_quality_gate_runtime` → `channel_part4_qualityfix` → `channel_part4_benchmark_hook`.

## Mutable-state invariant

`channel_part4_qualityfix` continues to import the canonical human-quality module directly. Therefore `HUMAN_GATE_VERSION`, `HUMAN_GATE_FINGERPRINT`, and `verify_hard_facts` still have one authoritative module object. Retirement removes the alias object path; it does not create or copy mutable state.

## Validation required before merge

The retirement PR must pass:
- migration-registry tests;
- focused human-quality-gate tests;
- focused quality-repair tests;
- translation benchmark freshness tests;
- full project validation;
- Maintenance diagnostics;
- Security / CodeQL;
- translation benchmark and fanfic validation;
- exact production-image validation when triggered.

After merge, a real `main` run must again complete state/database restore, project validation, runtime smoke, live provider checks, one complete monitor pass, checkpoint, encrypted recovery backup/outcome upload, and state/database persistence before the retirement is considered production-proven.

## Dependency decision

No additional GitHub repository or dependency is needed for this removal. The repository's existing LibCST, Grimp, Coverage.py, Ruff, Vulture, Complexipy, Deptry, CodeQL, production workflow, and image validation cover the relevant failure surface. Adding a new refactor tool here would expand supply-chain and maintenance risk without adding a missing safety gate.

## Next Stage C gate

Do not migrate `app.channel_part4_qualityfix` in this retirement change.

Only after this shim retirement is merged and independently production-proven should the next planning-first pass begin for `app.channel_part4_qualityfix`. That pass must start with fresh reference/import-order evidence and must preserve its mutation of the canonical human-quality-gate state.
