# Stage C closure audit — 2026-09-17

This audit starts only after the registered compatibility-shim subphase completed and production proved the zero-shim state on `main` commit `7c45308deeb22103b0a4380ea380f3ae850de6e5`.

## Purpose

Classify every remaining historical-name implementation module as exactly one of:

- **migrate later** — a semantic boundary is clear, focused coverage is adequate or can be made adequate with a bounded precursor test step, and a staged migration would materially improve maintainability; or
- **retain by design** — the current path/name is an intentional production contract or changing it would add more risk than architectural value.

This audit does not rename, move, delete, or rewrite any production module.

## Zero-shim baseline

Push-triggered Maintenance #75 / run `35246959622` on current `main` reported eight historical-name candidates, zero parse errors and `Success! No dependency issues found.` from Deptry. All eight candidates were runtime-linked in the current Grimp graph, so none was a dead-code deletion candidate.

The closure-audit draft PR then triggered Maintenance #76 / run `35252319432`. Artifact `10510660703` (`hani-maintenance-reports`, SHA-256 `9df94d559b7c68a163ac17aefb5700b9981e06e6a689956cfbc519c4f4cc73c7`) collected fresh per-test Coverage.py contexts and current Grimp evidence.

| Module | Direct importers | Downstream importers | Coverage | Test contexts | Closure decision |
| --- | ---: | ---: | ---: | ---: | --- |
| `app.channel_part4_benchmark_hook` | 1 | 1 | 78.7% | 2 | **retain by design** |
| `app.channel_part4_hardening` | 5 | 13 | 88.3% | 123 | **retain by design** |
| `app.channel_part4_humanfix` | 3 | 3 | 74.7% | 126 | **migrate later** |
| `app.channel_part4_qualityfix` | 1 | 1 | 99.1% | 129 | **migrate later** |
| `app.phase2_runtime_compat` | 1 | 1 | 81.7% | 8 | **retain by design** |
| `app.phase3_recovery` | 3 | 3 | 66.2% | 116 | **migrate later** |
| `app.phase3_recovery_hardening` | 1 | 1 | 80.8% | 26 | **migrate later** |
| `app.source_authority_hardening` | 3 | 6 | 80.6% | 28 | **retain by design** |

## Per-module decisions

### `app.channel_part4_benchmark_hook` — retain by design

This is not a generic production patch with an obsolete name. It is a deliberately narrow import-order hook for the cached translation benchmark. It activates only when `__main__.__spec__.name` is `tools.run_translation_benchmark_cached`, patches benchmark resume/checkpoint handling around the human-gate fingerprint, and otherwise returns without changing normal bot startup.

Fresh evidence is 78.7% coverage across two focused benchmark-freshness tests, with one direct importer and one downstream importer. A semantic rename would still require preserving the same unusual package-import timing and would not simplify normal production architecture. The current path therefore remains an intentional benchmark integration contract.

Reconsider only if the cached benchmark runner itself is redesigned or moved behind an explicit installer interface.

### `app.channel_part4_hardening` — retain by design

This module is now a central ChannelStyle fidelity layer rather than a small temporary fix. It is imported directly by five modules and has 13 downstream importers, including the real `app.__main__` chain through translation fusion. Fresh coverage is 88.3% across 123 test contexts.

It owns multiple tightly coupled behaviors: semantic number/date normalization, hard-fact verification, identity/speaker preservation, deterministic repairs and writer-level fidelity checks. Complexipy also identifies `semantic_number_tokens`, `verify_hard_facts`, and `_contains_historical_fact_leak` as substantial complexity hotspots.

A filename-only migration would create broad import churn without reducing that coupling. If maintainability work is later justified, behavior-preserving decomposition should come first; the current module path is retained during Stage C closure.

### `app.channel_part4_humanfix` — migrate later

The implementation has stabilized around a clear semantic responsibility: the human-quality gate and polish layer. It owns the `HUMAN_GATE_VERSION` / fingerprint, human-polish decision logic, metadata/speaker restoration, and source-authorized final quality checks. Fresh evidence is 74.7% coverage across 126 test contexts with three direct importers.

A later staged migration to a responsibility name such as `app.channel_human_quality_gate_runtime` would materially improve architecture because both benchmark freshness and the later quality layer depend on this gate as a named concept rather than on a generic “humanfix”.

Prerequisite: add focused coverage for currently less-exercised human-polish/client failure and fingerprint/update paths before changing imports. The migration must preserve import order and the exact human-gate fingerprint semantics.

### `app.channel_part4_qualityfix` — migrate later

This layer has a clear durable responsibility despite its temporary-looking filename: source-authorized quality repair for translation output and deterministic fallback. It normalizes bounded Japanese/identity mistranslations, restores source emoji/laughter, hardens the fallback translator, updates the human-gate fingerprint, and installs the final writer/verifier bindings.

Fresh evidence is 99.1% coverage across 129 test contexts with one direct importer. That makes it well protected for a later compatibility-preserving semantic migration, although its cross-module monkey patches mean installation order still matters.

A future target such as `app.channel_quality_repair_runtime` should be considered only after the human-quality-gate dependency has a stable semantic path, so the migration sequence does not create repeated importer churn.

### `app.phase2_runtime_compat` — retain by design

This module is explicitly a compatibility boundary rather than ordinary product logic. It preserves the released state schema contract and keeps observability instrumentation transparent to intentional lightweight test doubles by temporarily providing missing state methods around narrowly scoped calls.

Fresh evidence is 81.7% coverage across eight relevant test contexts with a single direct importer. It also mutates `SCHEMA_VERSION` and installs guards at import time. Renaming the module would not remove the underlying compatibility requirement, and changing that boundary purely to erase the historical “phase2” label would add schema/test risk without useful architectural gain.

Reconsider only if the lightweight test-double architecture or the associated backward-compatible state keys are deliberately redesigned.

### `app.phase3_recovery` — migrate later

This is a major durable X-recovery subsystem: resumable checkpoints, state schema integration, bounded retries, page/profile retrieval, cursor/checkpoint semantics and scheduled-source recovery. It has three direct importers and three downstream importers. Fresh execution coverage is 66.2%, but the module is exercised by 116 distinct test contexts.

The responsibility is semantically clear enough that the historical Phase 3 name should eventually be replaced by something like `app.x_resumable_recovery_runtime`. However, its state/checkpoint surface and comparatively lower line coverage make an immediate rename inappropriate.

Prerequisite: map and directly test critical uncovered checkpoint sanitization/state migration/retry branches before a planning-first migration. Any rename must preserve persisted checkpoint compatibility and exact installation order.

### `app.phase3_recovery_hardening` — migrate later

This module is a narrow integrity layer over the Phase 3 recovery engine. It deliberately makes unresolved checkpoints lossless/non-expiring and adds scoped user-ID recovery while patching three names on the base recovery module at import time.

Fresh evidence is 80.8% coverage across 26 test contexts with one direct importer. Its durable responsibility is clearer than the word “hardening”; a later semantic target such as `app.x_recovery_integrity_runtime` would be useful.

It belongs to the same recovery family as `phase3_recovery`. Do not migrate it independently in a way that creates two rounds of unnecessary importer changes. The base recovery migration plan and integrity-layer sequencing must be designed together, while changes remain split into reviewable focused steps.

### `app.source_authority_hardening` — retain by design

The name accurately describes an intentional runtime policy layer: it enforces the configured-source authority boundary across non-Fanfic X retrieval and patches `XCollector` so global/external authors cannot leak into collection, archive search or event recovery. It has three direct importers, six downstream importers, and 80.6% coverage across 28 test contexts.

Unlike a generic `*fix` filename, “source authority hardening” still communicates the actual security/completeness contract. A rename to another equivalent phrase would add import-order and recovery coupling risk without materially clarifying the architecture. Keep the current path unless the source-authority policy is later moved behind an explicit non-monkey-patched interface.

## Closure result

**Retain by design:**

- `app.channel_part4_benchmark_hook`
- `app.channel_part4_hardening`
- `app.phase2_runtime_compat`
- `app.source_authority_hardening`

**Migrate later:**

- `app.channel_part4_humanfix`
- `app.channel_part4_qualityfix`
- `app.phase3_recovery`
- `app.phase3_recovery_hardening`

No module is approved for direct deletion. No module is approved for an unattended rename. Each future migration remains a new planning-first Stage C change with fresh LibCST evidence, focused tests, compatibility handling where required, full CI, and post-merge production verification.

## Future ordering

1. Strengthen focused human-gate coverage around the less-exercised polish/client/fingerprint branches.
2. Migrate `channel_part4_humanfix` to a semantic human-quality-gate path in its own staged change.
3. Migrate `channel_part4_qualityfix` only after that dependency path is stable.
4. Treat `phase3_recovery` + `phase3_recovery_hardening` as one architectural family: improve critical recovery coverage first, then design a staged base/integrity semantic migration without mixing behavior changes.
5. Keep all four “retain by design” modules out of rename queues unless their underlying architecture changes.

This ordering is a roadmap, not authorization to combine migrations. One coherent structural step remains the maximum scope for a future PR.

## Tooling decision

Existing LibCST, Grimp, Coverage.py, Ruff, Deptry, Vulture, Complexipy and the native inventory provide the evidence required for closure. No additional GitHub refactor project or production dependency adds useful signal at this stage. Tach and Import Linter remain deferred to Stage D, after the intended package boundaries are stable enough to enforce rather than merely historical.
