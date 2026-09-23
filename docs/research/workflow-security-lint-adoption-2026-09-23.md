# Workflow security lint adoption — 2026-09-23

## Scope

This hardening slice implements the previously audited CI-only recommendation from `github-capability-scan-2026-09-18.md`. It does not change the Python runtime, Telegram/X behavior, production state, secrets, source ordering, delivery, or recovery semantics.

## actionlint

- Upstream: `rhysd/actionlint`
- License: MIT
- Reviewed release: `v1.7.12` (2026-03-30)
- Linux amd64 archive SHA-256: `8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`
- Role: blocking workflow correctness validation.
- Installation: CI-local release archive only; checksum verified before extraction; not a production dependency.
- Network behavior: bounded HTTPS download with retries/connect/max-time limits.

## zizmor

- Upstream: `zizmorcore/zizmor` and `zizmorcore/zizmor-action`
- License: MIT
- Reviewed zizmor release: `v1.29.0` (2026-08-01)
- Reviewed action release: `v0.6.2`, pinned to commit `3dc1ecc9bcb9e94e9b2c709687979e1298497054`.
- Role: report-only GitHub Actions security analysis during the observation/triage period.
- `online-audits` is disabled in the initial pilot, so the scanner does not need repository credentials for remote audit lookups.
- Advanced Security upload is disabled; findings are annotations/log output only.
- The job is `continue-on-error: true`; findings cannot block production until they have been triaged and an explicit promotion decision is recorded.

## Safety boundaries

1. The workflow runs only for workflow-file changes, main workflow-file pushes, or manual dispatch.
2. Permissions are `contents: read` only.
3. Checkout disables credential persistence for the zizmor job.
4. Both tools are CI-only and absent from production requirements.
5. No finding is auto-fixed; remediation requires a reviewed repository change.
6. actionlint is blocking because malformed workflow behavior is deterministic correctness evidence; zizmor remains report-only because security findings require repository-specific triage before enforcement.

## Rollback

Delete `.github/workflows/workflow-security-lint.yml`. No runtime/state migration is required.

## Promotion gate for zizmor

Do not make zizmor blocking merely because the pilot runs successfully. First collect and classify findings on the repository's real workflows, resolve true high-confidence issues, document intentional exceptions, and only then consider a separate enforcement PR.
