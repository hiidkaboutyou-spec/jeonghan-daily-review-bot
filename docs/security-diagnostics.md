# Security diagnostics and incident triage

Daily Hani already had strong runtime health machinery before this layer: structured production outcomes, a scheduled watchdog with bounded recovery decisions, source health tracking, Render validation, and optional privacy-scrubbed Sentry events. The purpose of this addition is therefore **not** to introduce another self-healing platform. It closes the pre-production/security gaps that the existing runtime watchdog cannot detect.

## Added checks

### Python dependency vulnerability audit

`Hani Security Diagnostics` builds a throwaway environment from the real production `requirements.txt` and audits the installed package set with `pip-audit`. The auditing tool itself is installed only in an isolated CI environment from the exact upstream Git commit recorded in `requirements-security.txt`; it is not part of the production image.

The audit runs on relevant pull requests and main pushes, once per day, and manually. A discovered vulnerability fails the check. The JSON report is retained as a short-lived GitHub Actions artifact so the exact package/advisory can be inspected during triage.

### Dependency Review

Pull requests also run GitHub's Dependency Review action and reject newly introduced vulnerabilities at `high` severity or above. License gating is deliberately disabled: during the 2026-09-15 audit, dependency-review-action v5 had active upstream bug reports involving unknown/incorrect license detection. This preserves the useful vulnerability gate without creating a known false-positive merge blocker.

### Bandit

Bandit scans `app/` and `tools/` for Python security problems. The merge gate is deliberately conservative: only findings that are both **high severity** and **high confidence** fail CI. The JSON report is uploaded for review. This avoids turning lower-confidence static-analysis noise into production churn while still catching the strongest signals.

### CodeQL

A separate `Hani CodeQL` workflow performs GitHub semantic code analysis for Python using `build-mode: none`, which is the supported mode for interpreted languages. It runs on relevant PR/main changes and weekly on a schedule. Its action is pinned to the audited CodeQL v4 commit rather than a floating tag.

## What was not added

- No new runtime Python dependency.
- No new production secret.
- No daemon, database, queue, or monitoring service.
- No second self-healing/retry loop.
- No OpenTelemetry stack: Hani already has structured logs, optional sanitized Sentry, Render evidence, source health, and production outcome tracking; another telemetry framework would duplicate current capability without solving the identified gap.
- No Datadog requirement: it remains optional if deeper hosted telemetry is wanted later, but it is not required for this diagnostic layer.

## Supply-chain controls

Third-party GitHub Actions used by the new workflows are pinned to full commit SHAs. Security scanners are kept in `requirements-security.txt`, separate from `requirements.txt`, and pinned to full upstream Git commits. The production Docker/runtime dependency graph is unchanged.

## Incident workflow

When a check fails, use `.agents/skills/hani-incident-triage/SKILL.md`. The intended sequence is evidence -> root cause -> focused fix branch -> tests/security checks -> PR -> post-merge production verification. Security checks must not be muted simply to restore a green status.

## Rollback

Removing this diagnostic layer requires only deleting the two new workflows, `requirements-security.txt`, the incident-triage skill, its tests, and this document, then removing the corresponding capability-manifest entries. No runtime state or database migration is involved.
