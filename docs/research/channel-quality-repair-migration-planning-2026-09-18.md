# Channel quality-repair migration planning — 2026-09-18

## Decision

Do not migrate `app.channel_part4_qualityfix` yet.

The semantic target remains `app.channel_quality_repair_runtime`, but a focused benchmark-freshness precursor is required before the compatibility migration. The precursor must bind human benchmark evidence to the current quality-repair implementation so a later code/path change cannot silently reuse stale completed benchmark cases.

## Production baseline

Planning starts from production-proven main commit:

`926eba77f0a42a56fc84a61ab60b56df63bb96fd`

That commit retired `app.channel_part4_humanfix` after successful real-main Bot, Render, Security, CodeQL, Maintenance, Fanfic, and workflow-triggered Watchdog validation. The quality-repair dependency therefore has a stable canonical human-gate path: `app.channel_human_quality_gate_runtime`.

## Fresh Maintenance evidence

Planning PR #101, head `d380bfcd3d68136ba530c192967e2ee35addc123`, triggered Hani Maintenance Diagnostics #98 / run `35331116142`.

Artifact:
- name: `hani-maintenance-reports`
- artifact id: `10540524470`
- digest: `sha256:c7deb3ef46a2cdbbeb8011936a1802ab725dd265c0aa43f8d2ab7c10dc04a56f`

Fresh module-family evidence for `app.channel_part4_qualityfix`:
- risk: high because it is import-time runtime patching;
- direct importers: 1;
- downstream importers: 1;
- direct runtime importer: `app`;
- current app chain: `app` → `app.channel_part4_qualityfix`;
- execution coverage: **99.1%**;
- test contexts: **129**;
- Deptry: `Success! No dependency issues found.`.

No extra coverage precursor is justified for the quality-repair implementation itself.

## Fresh LibCST plan

Read-only plan:

`app.channel_part4_qualityfix` → `app.channel_quality_repair_runtime`

Results:
- old module exists: true;
- proposed target exists: false;
- references: **4**;
- structurally safe references: **4**;
- manual-review references: **0**;
- dynamic string references: **0**;
- import-order-sensitive references: **1**;
- parse errors: **0**.

The four references are:
1. `app/__init__.py` — import-order-sensitive; preserve the current position after `channel_human_quality_gate_runtime` and before `channel_part4_benchmark_hook`;
2. `tests/test_channel_part4_qualityfix.py` module import;
3. `tests/test_channel_part4_qualityfix.py` package import used for patching `_BASE_TRANSLATE_LINE`;
4. `tests/test_translation_publishability.py` module import.

There is no production/workflow/CLI/config/dynamic-import caller of the historical name beyond the package initializer.

## Mutable-state and install-order contract

The current quality-repair layer is not a cosmetic helper. At import time it:
- wraps source-authorized laughter/emoji/identity/Japanese repair;
- hardens the deterministic fallback translator;
- replaces `translation.ChannelStyleCaptionWriter`;
- replaces verifier bindings in hardening/runtime/translation;
- mutates `app.channel_human_quality_gate_runtime.HUMAN_GATE_VERSION`;
- mutates `HUMAN_GATE_FINGERPRINT`;
- mutates `verify_hard_facts`.

Any semantic migration therefore needs a same-module-object compatibility alias until later retirement and must keep the exact install order:

`channel_part4_hardening` → `channel_source_fact_normalization_runtime` → `channel_human_quality_gate_runtime` → quality repair → `channel_part4_benchmark_hook`.

## Newly discovered benchmark-freshness gap

`tools/run_translation_benchmark_human.py` documents `_production_fingerprint()` as binding completed benchmark cases to the code that produced and judged them.

Its current `_PRODUCTION_FINGERPRINT_PATHS` includes `app/channel_human_quality_gate_runtime.py`, but it does **not** include `app/channel_part4_qualityfix.py`.

That is a real evidence gap because the omitted quality-repair module changes the production writer/verifier/fallback behavior and human-gate fingerprint. A quality-repair code change can therefore rely only on manually bumping the version instead of being cryptographically bound into the production benchmark fingerprint.

The current freshness test protects the canonical human-gate file path but has no equivalent assertion for the quality-repair file.

## Required precursor

Before the semantic migration:

1. add the current `app/channel_part4_qualityfix.py` to `_PRODUCTION_FINGERPRINT_PATHS`;
2. add a regression assertion that the production fingerprint tracks the current quality-repair implementation;
3. retain the existing test proving the hash changes when tracked quality code changes;
4. run the full benchmark/freshness and project validation gates;
5. merge and independently verify that benchmark/production workflows remain healthy.

Do not rename the module in that precursor PR.

After the precursor is production-proven, the semantic migration may:
- create `app/channel_quality_repair_runtime.py` with behavior unchanged;
- reduce `app/channel_part4_qualityfix.py` to a same-module-object alias;
- migrate the four structural references while preserving local binding names and exact order;
- update the benchmark fingerprint path from the historical file to the canonical quality-repair file;
- register the compatibility migration and later-removal gates;
- require full CI and post-merge production proof.

## Tooling decision

No new dependency or GitHub refactoring project is justified. The current LibCST 1.9.0 planner, Grimp, Coverage.py, Ruff, Vulture, Complexipy, Deptry, CodeQL, benchmark workflow, and production gates cover the failure surface. Keep Rope/Tach/Import Linter deferred according to the repository maintenance policy.
