# Stage C closure audit — 2026-09-17

This audit starts only after the registered compatibility-shim subphase completed and production proved the zero-shim state on `main` commit `7c45308deeb22103b0a4380ea380f3ae850de6e5`.

## Purpose

Classify every remaining historical-name implementation module as exactly one of:

- **migrate later** — a semantic boundary is clear, focused coverage is adequate, and a staged migration would materially improve maintainability; or
- **retain by design** — the current path/name is an intentional production contract or changing it would add more risk than architectural value.

This audit does not rename, move, delete, or rewrite any production module.

## Zero-shim baseline

The latest push-triggered Maintenance run on current `main` reports eight historical-name candidates and no parse/dependency problems. Per-test Coverage contexts are intentionally unavailable on push runs, so the draft closure-audit PR exists to obtain a fresh PR-triggered Coverage/Grimp artifact before any classifications are finalized.

Current candidates from Maintenance #75 / run `35246959622`:

1. `app.channel_part4_benchmark_hook`
2. `app.channel_part4_hardening`
3. `app.channel_part4_humanfix`
4. `app.channel_part4_qualityfix`
5. `app.phase2_runtime_compat`
6. `app.phase3_recovery`
7. `app.phase3_recovery_hardening`
8. `app.source_authority_hardening`

All eight are runtime-linked in the current Grimp graph. Therefore none is a dead-code deletion candidate.

The zero-shim artifact also reports `Success! No dependency issues found.` from Deptry. Vulture findings remain unrelated report-only candidates and do not authorize removal.

## Required evidence before final classification

- fresh PR-triggered per-test Coverage contexts for all eight modules;
- current Grimp direct/downstream importer counts and entrypoint chains;
- direct inspection of each implementation's import-time mutation/monkey-patch behavior;
- focused tests and repository history where useful;
- workflow/config/dynamic reference review where relevant;
- explicit comparison of maintenance value versus compatibility/import-order risk.

Existing LibCST, Grimp, Coverage.py, Ruff, Deptry, Vulture and Complexipy tooling is sufficient for this audit. No external refactor package should be added merely for classification.

## Decision status

Pending fresh PR Maintenance evidence. Final classifications and rationale will be recorded here and summarized in `docs/repository-maintenance.md` before this audit PR is considered complete.
