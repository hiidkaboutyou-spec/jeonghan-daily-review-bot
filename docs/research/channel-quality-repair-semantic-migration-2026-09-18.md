# Channel quality-repair semantic migration — 2026-09-18

## Scope

Semantic Stage C migration:

`app.channel_part4_qualityfix` → `app.channel_quality_repair_runtime`

This change is structural only. The quality-repair implementation is copied unchanged to the canonical module. The historical path becomes a same-module-object compatibility alias and is not removed in this migration.

## Preconditions

The dependency path `app.channel_human_quality_gate_runtime` is retired-path-clean and production-proven.

PR #101 completed fresh planning:
- 99.1% execution coverage;
- 129 test contexts;
- one direct runtime importer (`app`);
- LibCST references: 4;
- structurally safe references: 4;
- manual/dynamic references: 0;
- import-order-sensitive references: 1 (`app/__init__.py`);
- parse errors: 0.

The four structural references were `app/__init__.py`, two focused quality-repair test imports, and `tests/test_translation_publishability.py`.

PR #102 then closed the benchmark-freshness precursor by binding `app/channel_part4_qualityfix.py` into the human benchmark production fingerprint. It merged as `247374faf284c8ccf704b520819140408013ec3b`.

Independent main proof for that precursor is green:
- Jeonghan Daily Review Bot #4175;
- Render Production Validation #290;
- Hani Security Diagnostics #102;
- Hani CodeQL #88;
- Hani Maintenance Diagnostics #102;
- Nightly Jeonghan Fanfic Digest #945;
- workflow-triggered Jeonghan Daily Watchdog #3277.

## Responsibility boundary

The canonical quality-repair runtime owns the durable source-authorized repair/fallback responsibility:
- exact laughter-count fidelity;
- bounded Japanese literalism normalization;
- source-authorized Jeonghan identity normalization;
- restoration of missing source emoji/laughter in deterministic fallback;
- safe exact-line fallback translations;
- final writer-level deterministic repair;
- installation of final writer/verifier bindings;
- mutation of the canonical human-quality-gate version/fingerprint/verifier state.

The semantic name describes that responsibility and removes the historical PART 4 / "qualityfix" implementation-phase label.

## Compatibility design

`app/channel_part4_qualityfix.py` is reduced to a same-module-object alias using `sys.modules[__name__] = _implementation`.

This preserves compatibility for any unseen historical importer and prevents split module globals. The canonical implementation remains the only implementation body.

## Import-order invariant

The package initializer must retain exactly this relative order:

1. `channel_part4_hardening`
2. `channel_source_fact_normalization_runtime`
3. `channel_human_quality_gate_runtime`
4. `channel_quality_repair_runtime`
5. `channel_part4_benchmark_hook`

The package-local binding name `_channel_part4_qualityfix` is intentionally preserved during the compatibility phase to minimize unrelated churn.

## Human-gate mutable-state invariant

The canonical quality-repair runtime continues to mutate:
- `HUMAN_GATE_VERSION`;
- `HUMAN_GATE_FINGERPRINT`;
- `verify_hard_facts`;

on the single canonical `app.channel_human_quality_gate_runtime` module object.

No second human-gate implementation or state copy is introduced.

## Benchmark freshness

The human benchmark production fingerprint now tracks the canonical quality-repair implementation file:

`app/channel_quality_repair_runtime.py`

It must not track the small compatibility shim. This ensures a real change to quality-repair code invalidates stale completed benchmark evidence while a later shim-only retirement does not pretend to be production quality behavior.

## Migration registry

The legacy/canonical pair is registered as one active `compatibility-shim` migration with explicit later-removal gates. No other active compatibility migration may be introduced while this one is open.

## Non-changes

This migration does not intentionally change:
- repair algorithms or fallback text;
- human-polish behavior;
- provider behavior;
- Telegram delivery;
- persisted state/schema;
- schedules or secrets;
- production dependencies.

## Required validation

Before merge:
- focused quality-repair tests;
- human-quality-gate tests;
- translation publishability tests;
- benchmark-freshness tests;
- migration-registry tests;
- full project validation;
- Maintenance;
- Security/CodeQL;
- translation benchmark/fanfic;
- Render production-image validation.

After merge, require an independent real-main production run through state/database restore, project validation, runtime smoke, live providers, complete monitor pass, checkpoint, encrypted backup/outcome upload, state/database persistence, and workflow-triggered Watchdog.

Only after that proof may a later focused PR consider retiring `app.channel_part4_qualityfix`.
