# GitHub Actions workflow-safety rollout — 2026-09-18

## Decision

Add two CI-only workflow analyzers. Neither tool enters `requirements.txt` or production runtime.

### actionlint

- upstream: `rhysd/actionlint`
- pinned version: `v1.7.12`
- tag commit: `914e7df21a07ef503a81201c76d2b11c789d3fca`
- install method: `go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12`
- enforcement: blocking

Purpose:
- workflow YAML/schema correctness;
- GitHub expression type checking;
- action input/output checks;
- reusable-workflow contract checks;
- cron/glob/needs checks;
- shellcheck integration.

The CI job uses pinned `actions/setup-go@44694675825211faa026b3c33043df3e48a5fa00` (v6.0.0) and Go's module checksum verification rather than running an unpinned download script.

### zizmor

- upstream: `zizmorcore/zizmor`
- pinned analyzer: `v1.29.0`
- analyzer tag commit: `3c116961091b50bd1a08ffefe916469d4d90093c`
- official action: `zizmorcore/zizmor-action@3dc1ecc9bcb9e94e9b2c709687979e1298497054` (v0.6.2)
- enforcement: report-only initial rollout
- online audits: disabled
- Advanced Security upload: disabled
- requested workflow permissions: `contents: read` only

Purpose:
- template/script injection risks;
- excessive workflow permissions;
- credential persistence/leakage;
- dangerous action/ref patterns;
- workflow-specific supply-chain risks.

The initial report-only mode is intentional. Existing findings must be triaged before any blocking threshold is enabled, so adding a scanner cannot accidentally stop production schedules.

## Safety boundary

- no production package changes;
- no Telegram/X/AO3/provider behavior changes;
- no new secrets;
- no write permission;
- no workflow auto-fix;
- no scanner may mutate repository content.

After the first successful run, review findings. High-confidence actionable findings should be fixed in focused PRs, then zizmor can be promoted gradually to a blocking high-severity/high-confidence gate.

## First-run findings and remediation

Initial Hani Workflow Safety run #1 (`35342566168`) proved both scanners are operational.

### actionlint

The blocking actionlint job installed v1.7.12 successfully and found one existing ShellCheck style issue (`SC2005`) in the FFmpeg bootstrap inside `.github/workflows/main.yml`. The shell was simplified without changing behavior.

### zizmor

The first offline, high-confidence scan reported **30 high-severity findings**:
- **29 `unpinned-uses` findings** across Daily Watchdog, Fanfic, main, Render, Rust Editorial Core, and Translation Benchmark workflows;
- **1 `bot-conditions` finding** for the Daily Watchdog's `github.actor == 'github-actions[bot]'` authorization-style condition.

The 29 floating references were replaced manually with verified immutable commits:
- `actions/checkout` → `d23441a48e516b6c34aea4fa41551a30e30af803`;
- `actions/setup-python` → `ece7cb06caefa5fff74198d8649806c4678c61a1`;
- `actions/cache/restore` and `actions/cache/save` → `caa296126883cff596d87d8935842f9db880ef25` (current v5 ref inspected 2026-09-18);
- `actions/upload-artifact` → `ea165f8d65b6e75b540449e92b4886f43607fa02`;
- `dtolnay/rust-toolchain` → `6bed0761d98439e5a578e2877258200ad565ba87`, while explicitly retaining `toolchain: stable`.

No zizmor auto-fix was applied.

The remaining `bot-conditions` finding is deliberately **not** changed inside this supply-chain pinning pass. Daily Watchdog participates in production recovery/re-arm behavior; its authorization/trigger contract requires a focused audit of `daily_watchdog_runner.py`, dispatch provenance, and GitHub API validation before changing the condition. Keep zizmor report-only until that focused hardening is complete.

