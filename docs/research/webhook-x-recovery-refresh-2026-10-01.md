# Webhook X recovery refresh — 2026-10-01

## Preflight
- canonical main: df9c7c043551e27a00b05130ad856a5628fcc367
- stale implementation PR: #157, 20 commits behind current main and exact-head Translation Benchmark failed
- related blockers: #151 Telegram always-on ingress, #156 no-cookie webhook X recovery, #158 deterministic translation-repair fidelity
- latest main already includes #159 @haniwadda priority full-feed and #160 webhook search replay fix

## Root cause
The always-on webhook can have no X auth_token/ct0. Generic provider recovery is intentionally env-driven, while webhook startup did not infer degraded mode from its concrete Settings and did not install the same degraded recovery hardening as Daily.

## Research queries
- GitHub Twitter scraping FxTwitter profile statuses cursor fallback rate limit GitHub Actions 403 2026
- GitHub x-tweet-fetcher FxTwitter Nitter multi backend Twitter scraper
- GitHub Actions scheduled workflows delayed dropped official documentation

## Options reviewed
1. Existing FxTwitter/FxEmbed provider: chosen as first degraded provider; already integrated, MIT upstream, no new secret/dependency, and prior project shadow evidence reached every configured source. Still partial authority only.
2. x-tweet-fetcher: useful multi-backend/error-classification architecture; rejected as dependency because it duplicates Hani normalization/state/provider plumbing.
3. Nitter/self-hosted Nitter: useful secondary architecture reference but adds hosting/session/account risk and is not needed for this repair.
4. Scheduled GitHub Actions polling: rejected for Telegram responsiveness; GitHub documents schedule delay/drop under load.
5. Authenticated scrapers/Twikit-style paths: rejected for this no-cookie repair because they reintroduce account/session risk.

## Refreshed implementation
Fresh branch from current main: `fix/webhook-x-recovery-refresh-20261001`.
- webhook startup enters degraded recovery only when concrete X cookies are absent and no explicit provider state exists;
- webhook installs existing degraded recovery runtime before constructing the assistant;
- degraded public timeline tries FxTwitter first, then existing syndication, then the outer Agent Reach fallback;
- public fallback remains partial and cannot claim authenticated completeness or advance the full-success cursor;
- no new dependency, token, paid API, state schema, or bypass.

Regression tests from #157 were reapplied on the fresh branch. The unrelated translation-repair change from stale #157 was deliberately not carried into this X repair; #158 remains independently tracked.

## Acceptance
Do not merge until exact-head CI on the fresh PR is green. After merge, deploy the exact main SHA to the always-on host and validate a real owner `🕑 ۲ ساعت اخیر` press with provider logs. Useful fallback rows are not proof of full X completeness.
