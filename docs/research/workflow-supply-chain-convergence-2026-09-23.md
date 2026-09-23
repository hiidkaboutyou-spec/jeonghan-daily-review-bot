# Workflow supply-chain convergence — 2026-09-23

## Goal

Close the remaining high-confidence GitHub Actions credential-persistence findings and add a pull-request-time dependency admission gate without changing Hani runtime behavior, production state, Telegram delivery, X collection, schedules, secrets, or recovery semantics.

## Repository evidence

The exact-head zizmor run for the bot-identity hardening work no longer reported the old name-based bot authorization finding. It did report checkout credential persistence in twelve checkout steps across:

- `.github/workflows/codeql.yml`
- `.github/workflows/fic-digest.yml`
- `.github/workflows/main.yml`
- `.github/workflows/maintenance-diagnostics.yml`
- `.github/workflows/render-validate.yml`
- `.github/workflows/rust-editorial-core.yml`
- `.github/workflows/security-diagnostics.yml`
- `.github/workflows/translation-benchmark.yml`

Inspection found no authenticated Git write after those checkouts. The workflows that call the GitHub API or dispatch another workflow already pass `GH_TOKEN: ${{ github.token }}` explicitly. Therefore checkout does not need to persist credentials into Git configuration.

This branch sets `persist-credentials: false` on all twelve remaining checkout steps and adds a repository-level regression test that fails if a future checkout omits it.

## Dependency Review adoption

Adopt GitHub's official `actions/dependency-review-action` as a PR-only gate.

Reviewed version:

- repository: `actions/dependency-review-action`
- release: `v5.0.0`
- pinned commit: `a1d282b36b6f3519aa1f3fc636f609c47dddb294`
- trigger: pull requests targeting `main`
- permissions: `contents: read` only
- threshold: fail only when a newly introduced dependency has a known **high** or **critical** severity vulnerability
- runtime/state impact: none

GitHub documents Dependency Review for public repositories and describes it as a pre-merge control for vulnerabilities introduced by dependency changes. Version 5 uses Node 24 and requires Actions Runner 2.327.1 or newer; this project uses GitHub-hosted `ubuntu-latest` runners rather than a pinned self-hosted runner.

The existing `pip-audit` jobs remain useful and are not replaced: they audit the installed dependency environment. Dependency Review covers a different boundary — what a pull request is trying to introduce before it lands.

## External tools reviewed but not installed

### StepSecurity Harden-Runner

Deferred. It can observe processes/files/egress and optionally enforce egress allowlists on GitHub-hosted runners, but that adds a new runtime security agent and network-control layer to every selected CI job. Current upstream issue history includes block-mode reliability/fail-open concerns. Hani already has pinned Actions, actionlint, zizmor, CodeQL, Bandit, pip-audit, minimal workflow permissions, and explicit provider/runtime boundaries. There is no current measured incident that justifies adding this extra execution/network dependency.

### OpenSSF Scorecard

Deferred. It is useful for repository-wide supply-chain posture, but the project already has CodeQL, dependency auditing, pinned actions and workflow linting. Publishing Scorecard results introduces additional OIDC/write-permission considerations. Revisit after the current workflow findings converge.

### pinact

Not installed. The repository already pins third-party and first-party Actions to full commit SHAs, and the new regression/security lint gates cover the demonstrated failure mode. A second pin-management tool would add process overlap without closing a current gap.

## Validation and rollback

Validation for this branch must include:

1. exact-head actionlint;
2. exact-head report-only zizmor, confirming the credential-persistence findings are gone;
3. the repository baseline unit suite, including the new checkout regression test;
4. existing security/CodeQL/maintenance checks;
5. the Dependency Review job itself.

No live credentials are required by the new gate and no production state migration exists. Rollback is limited to reverting the workflow/test/document changes. If Dependency Review is unavailable because the repository dependency graph is disabled or GitHub has a platform outage, leave the PR unmerged until the repository setting or platform condition is understood; do not weaken the production runtime to accommodate CI.
