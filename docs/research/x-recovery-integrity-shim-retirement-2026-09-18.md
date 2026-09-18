# X recovery integrity shim retirement — 2026-09-18

## Decision

The historical compatibility path `app.phase3_recovery_hardening` is now eligible for focused retirement. The canonical implementation remains `app.x_recovery_integrity_runtime`.

## Independent main proof

PR #111 merged to main as `e4faaaa886653d0bd9bbd3f5b1de49b6370a50d3` after the semantic migration. The real-main post-merge workflows are green, including Hani Maintenance Diagnostics #130, Security #138, Render Production Validation #307, Workflow Safety #15, CodeQL #99, and independent Jeonghan Daily Watchdog #3316.

Maintenance artifact `10553344798` (`sha256:c271d5072182d2bd340d98c2127791346e098512f6ef7302452879e6a5499570`) provides the fresh final audit:
- legacy references: 6
- structural references: 0
- dynamic/manual references: 6
- import-order-sensitive references: 0
- parse errors: 0
- direct importers: 0
- downstream importers: 0

The six remaining strings are non-runtime evidence: four synthetic historical-name fixtures in `tests/test_module_family_evidence.py` and two migration-registry assertions in `tests/test_module_migrations.py`.

## Removal scope

Retirement must remain focused:
- delete only the historical alias module;
- preserve `app.x_recovery_integrity_runtime` unchanged;
- mark the registry entry retired and retain this document as its retirement record;
- convert active-shim tests to retirement assertions;
- preserve the four synthetic maintenance fixtures as historical-name test data;
- do not alter checkpoint schema/identity, retry/cursor semantics, provider behavior, Telegram, AO3, secrets, schedules, dependencies, or import order of the canonical X recovery stack.

## Third-party research decision

No new third-party runtime dependency is justified for this retirement. The repository already has the appropriate read-only structural tooling (LibCST-based planning, Grimp import graph evidence, coverage evidence, Ruff, Deptry, Vulture and Complexipy). Adding another package at a compatibility-removal boundary would increase supply-chain and runtime surface without improving the proof needed for this change.

## Next gate

After this focused retirement is merged and independently production-proven, run a fresh Stage C inventory before selecting another historical module family. Do not combine the next semantic migration with this shim removal.
