# Channel quality-repair compatibility-shim retirement — 2026-09-18

## Decision

Retire and delete the historical compatibility path:

`app.channel_part4_qualityfix`

The canonical implementation remains:

`app.channel_quality_repair_runtime`

This retirement does not modify the canonical quality-repair implementation.

## Production-proven prerequisite

The semantic migration completed in PR #103 and merged as:

`e95828463d60eca3678e741b1ac128c3547fc018`

Independent real-main validation on that exact SHA succeeded:

- Jeonghan Daily Review Bot #4177 / run `35332411762`
  - state cache restore: success
  - private-review DB restore: success
  - project validation: success
  - runtime smoke: success
  - live production providers: success
  - one complete automatic monitor pass: success
  - DB checkpoint: success
  - encrypted recovery backup creation/upload: success
  - production outcome upload: success
  - state cache persistence: success
  - DB cache persistence: success
- Render Production Validation #292 / run `35332411756`: success
- Hani Security Diagnostics #104 / run `35332411743`: success
- Hani CodeQL #90 / run `35332411666`: success
- Hani Maintenance Diagnostics #104 / run `35332411732`: success
- Nightly Jeonghan Fanfic Digest #947 / run `35332411656`: success
- workflow-triggered Jeonghan Daily Watchdog #3279 / run `35332709185`: success

The canonical quality-repair path is therefore independently production-proven before shim removal.

## Fresh main-SHA Maintenance evidence

Hani Maintenance Diagnostics #104 ran on the same production main SHA and uploaded artifact:

- artifact id: `10541986429`
- name: `hani-maintenance-reports`
- digest: `sha256:a7207fe681e904ed1f26864c18afe1a60224ee8b11069ebd4036e55f1bca7ad1`

Fresh LibCST retirement evidence:

`app.channel_part4_qualityfix` → `app.channel_quality_repair_runtime`

- old module exists: true
- canonical target exists: true
- references: **2**
- structurally safe references: **0**
- manual-review references: **2**
- dynamic string references: **2**
- import-order-sensitive references: **0**
- parse errors: **0**

The only two historical-name references are the explicit migration-registry/identity strings in `tests/test_module_migrations.py`. Neither is a runtime caller.

Fresh Grimp/module-family evidence:
- direct importers of the historical shim: **0**
- downstream importers: **0**
- no runtime chain reaches the historical path
- Deptry: `Success! No dependency issues found.`

The push Maintenance run intentionally omits per-test Coverage contexts, but the final PR-head migration Maintenance run #103 previously measured the shim at 100% in exactly one context: the compatibility module-identity test. That assertion is obsolete once the path is retired; canonical quality-repair behavior remains directly covered by the focused quality-repair, publishability, human-gate, benchmark-freshness, and full validation suites.

## Canonical invariants before removal

`app/__init__.py` imports only `app.channel_quality_repair_runtime`, at the same exact install position between `channel_human_quality_gate_runtime` and `channel_part4_benchmark_hook`.

`tools/run_translation_benchmark_human.py` fingerprints `app/channel_quality_repair_runtime.py` and does not fingerprint the compatibility shim.

The canonical quality-repair runtime remains responsible for:
- source-authorized laughter/emoji/identity/Japanese repair;
- deterministic fallback hardening;
- final writer/verifier installation;
- mutation of the canonical human-quality-gate `HUMAN_GATE_VERSION`, `HUMAN_GATE_FINGERPRINT`, and `verify_hard_facts`.

Removing the shim therefore removes no production implementation or benchmark freshness input.

## Retirement change

The focused retirement:
1. deletes only `app/channel_part4_qualityfix.py`;
2. marks its migration registry entry `retired`;
3. changes migration tests back to zero active compatibility shims and lets the generic retired-path test prove that the old module cannot import while the canonical path still can;
4. removes the recurring Maintenance LibCST plan for the now-retired path;
5. records this evidence in repository guidance.

No production algorithm, provider, state/schema, Telegram, schedule, secret, dependency, or canonical import order is changed.

## Next Stage C boundary

After this retirement is independently production-proven, the next deferred migration family is the X recovery pair:

- `app.phase3_recovery`
- `app.phase3_recovery_hardening`

Do not migrate either opportunistically. The closure audit requires strengthening critical checkpoint/state/retry coverage first and designing the base/integrity migration sequence together while keeping each implementation change reviewable and focused.
