# Channel source-fact compatibility-shim retirement evidence — 2026-09-17

This note records the evidence used to retire the historical `app.channel_part4_finalfix` compatibility path while keeping `app.channel_source_fact_normalization_runtime` as the only supported implementation path.

## Scope

The retirement removes only the compatibility module file. It does not change source-authorized term normalization, English ordinal handling, ChannelStyle writer classes, hard-fact verification, fallback translation, human-gate fingerprints, provider collection, persisted state/database schemas, Telegram delivery, schedules, secrets, or production dependencies.

The canonical runtime remains installed from `app/__init__.py` immediately after `channel_part4_hardening` and before `channel_part4_humanfix`, `channel_part4_qualityfix`, and `channel_part4_benchmark_hook`.

## Migration baseline

PR #80 introduced the semantic module `app.channel_source_fact_normalization_runtime` and converted `app.channel_part4_finalfix` into a same-module-object compatibility alias.

The migration evidence recorded in PR #80 included:

- one direct production importer before migration (`app` / `app/__init__.py`);
- one downstream importer;
- 100% measured execution coverage across 12 test contexts for the historical implementation before ownership moved;
- two structural LibCST references before migration: the order-sensitive package initializer and the focused behavioral test;
- seven dynamic/manual historical-name references, all inspected as synthetic maintenance-test fixtures;
- zero parse errors.

The migration preserved the exact import/install position and moved the focused behavioral test to the canonical module.

## Fresh pre-removal evidence

The retirement branch starts from `main` commit `4a2d6056586406620ff01cdef4cdf7bf9503a374`.

`Hani Maintenance Diagnostics` #66 / run `35236940548` on that exact `main` commit produced artifact `10504120249` (`hani-maintenance-reports`, SHA-256 `e97890c82e368fd936e2386f4b3694e9c94a70f7a3a76c551800b4d8882dfbb4`).

Its read-only `stage-c-channel-source-fact-normalization-plan.json` reported:

- old module exists: `true`;
- canonical module exists: `true`;
- reference count: `8`;
- structurally safe references: `0`;
- manual-review references: `8`;
- dynamic-string references: `8`;
- import-order-sensitive references: `0`;
- parse errors: `0`;
- source mutation: `false`;
- automatic apply: `false`.

All eight remaining references were inspected. They are test/registry strings rather than runtime imports:

- three historical-name fixture strings in `tests/test_module_family_evidence.py`;
- four historical-name fixture strings in `tests/test_repo_structure_inventory.py`;
- one migration-registry assertion string in `tests/test_module_migrations.py`.

The fixture strings intentionally remain after retirement because they test the maintenance tools' ability to recognize a historical filename. They do not import or execute the retired module.

The same maintenance artifact's Grimp evidence reported for the shim:

- direct importers: none;
- downstream importer count: `0`;
- entrypoint chains: none;
- its only direct dependency was `app.channel_source_fact_normalization_runtime`.

Coverage was unavailable in that push-triggered Maintenance run by workflow design; the earlier migration evidence from PR #80 supplies the direct coverage proof. The current focused tests continue to import the canonical module directly.

The same artifact's Deptry report ended with `Success! No dependency issues found.`

## Production proof before retirement

The canonical path has been the production package import since PR #80. The current `main` production run #4073 / run `35236940427`, on commit `4a2d6056586406620ff01cdef4cdf7bf9503a374`, completed successfully through:

- state and private-review database restore;
- project validation;
- runtime smoke check;
- live production provider checks;
- one complete automatic monitor pass;
- database checkpoint;
- encrypted recovery backup creation and upload;
- production outcome upload;
- state cache persistence;
- private-review database cache persistence.

This proves the production path is already operating through the canonical package import without requiring the historical module as a caller-facing entrypoint.

## Behavioral safety net

`tests/test_channel_part4_final_edges.py` imports `app.channel_source_fact_normalization_runtime` directly and protects the established behavior:

- English `first` and Persian ordinal semantics remain equivalent where they represent the same source fact;
- sentence-initial discourse use of `First, ...` is not misclassified as a numeric fact;
- changed ordinal facts still fail verification;
- source-authorized Jeonghan prose canonicalization still occurs;
- hashtags remain untouched.

The canonical implementation itself is unchanged by retirement.

## Retirement change

The focused retirement performs only compatibility cleanup:

1. delete `app/channel_part4_finalfix.py`;
2. keep `app/channel_source_fact_normalization_runtime.py` unchanged;
3. keep its `app/__init__.py` import order unchanged;
4. mark the migration `retired` in `config/module_migrations.json` while preserving its historical removal gates and pointing to this record;
5. update migration-registry tests so active shims must still share one module object, while retired legacy paths must be absent and canonical paths must still import;
6. stop generating/uploading a recurring LibCST plan for this retired migration while retaining plans for the three still-active app shims;
7. update durable maintenance/migration guidance so future cleanup does not recreate the retired path.

The private package-local binding name `_channel_part4_finalfix` in `app/__init__.py` is deliberately left unchanged in this focused retirement. It points directly to the canonical module and does not create or import the retired module path. Renaming that private binding would be a separate cleanup concern and is not required to remove the legacy module path safely.

## Required validation

Before merge, the final retirement head must pass:

- focused Channel source-fact tests;
- migration-registry tests;
- full project validation and unittest suite;
- Maintenance Diagnostics with PR Coverage.py contexts;
- Security diagnostics;
- CodeQL;
- Channel Style Translation Benchmark;
- Nightly Fanfic validation;
- exact Render production-image validation.

After merge, `main` must again complete a real production monitor pass with persistence/recovery steps and the workflow-triggered Daily watchdog must remain healthy. The final post-merge run IDs are recorded on the retirement pull request so the repository file does not require a second behavior-free follow-up commit merely to append CI numbers.
