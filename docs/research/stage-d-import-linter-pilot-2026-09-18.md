# Stage D Import Linter pilot — 2026-09-18

## Why this tool

Import Linter 2.15 is the first Stage D architecture-boundary pilot.

Upstream evidence reviewed before adoption:
- release 2.15 published 2026-09-04;
- release commit `31927f1457e3df673912cb5efb0afa6dbc37585f`;
- Production/Stable classifier;
- Python >=3.10;
- dependency on Grimp >=3.17, matching Hani's existing pinned Grimp 3.17;
- built-in forbidden, protected, layers, independence, and acyclic-siblings contracts;
- BSD-2-Clause license.

Tach remains deferred. Its current reviewed latest release is v0.35.0 and it introduces a separate architecture model/toolchain. Do not install Tach in parallel with this pilot.

## Pilot boundary

The first contract is intentionally narrow:

`app.x_recovery_integrity_runtime` is a protected module and its only allowed direct production importer is the `app` package initializer.

Rationale:
- the integrity runtime is an import-time installer that patches the canonical `app.x_resumable_recovery_runtime` object;
- current production initialization deliberately owns its order in `app/__init__.py`;
- the Stage C migration/retirement evidence already proved the historical path was unnecessary and the canonical integrity layer should not become a general-purpose import target;
- protecting direct import ownership detects architectural drift without changing runtime behavior.

## Safety properties

- `import-linter==2.15` is in `requirements-maintenance.txt` only.
- Production `requirements.txt` and Docker dependencies are unchanged.
- Security Diagnostics now audits the installed maintenance dependency environment separately with `pip-audit`/OSV on Python 3.11, in addition to the unchanged production dependency audit.
- The pilot uses `--no-cache` because Import Linter/Grimp file caching is not concurrency-safe.
- The contract contains no `ignore_imports`.
- `tests/test_architecture_contract_config.py` locks the exact one-contract scope and rejects hidden ignore rules.
- CI captures `import-linter-report.txt` and records the tool exit status.
- The pilot is report-only. A contract violation does not fail CI in this introduction PR.

## Promotion gate

Do not make this contract blocking until all of the following are true:
1. the PR pilot report is kept/green and operationally valid;
2. the same report is stable on a real `main` Maintenance run;
3. dynamic imports and import-time patch ownership are reviewed separately;
4. no broad ignore rule is required;
5. no production runtime/state/provider/delivery behavior changes are bundled with promotion.

## Expansion gate

Do not add a second contract merely because Import Linter is installed.

A new contract requires:
- a documented architectural invariant;
- current Grimp/import evidence proving the invariant already exists;
- focused tests where appropriate;
- a report-only observation period before enforcement;
- no duplicate graph/enforcement framework unless Import Linter proves insufficient.

## Protected production invariants

This pilot must not change:
- `x_retrieval_checkpoints`;
- checkpoint version, identity, sanitation, or normalization;
- retry/fallback accounting;
- source authorization;
- cursor advancement;
- provider behavior;
- Telegram/AO3 delivery;
- schedules;
- secrets;
- production dependencies.
