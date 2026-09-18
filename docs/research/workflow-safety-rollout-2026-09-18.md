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
