# @haniwadda priority full-feed source

Owner request: add https://x.com/haniwadda/status/2105187135448138066?s=46 as an important account whose whole feed is collected.

Preflight: main at 97e77ce77a211c74d6a7b05c8d810d6bae1a160d; latest Daily/Watchdog runs successful. Reviewed open PRs (including #157 X recovery and stale #49 source addition), AGENTS.md, evidence-first-engineering, hani-production-acceptance, Settings.load, SourceModeGate, collector source boundary and source-mode regressions. LAUNCH_STATUS contains stale runtime/HEAD information; current GitHub state takes precedence. Machine-local Project Memory and a local shell/Python runner are unavailable in this session.

Implementation: add @haniwadda once in config/jeonghan_priority_x_sources.json, enabled=true, priority=10 (same as primary full-feed accounts), include_replies=true, mode=full_feed. Settings.load combines both source files. Full-feed bypasses the Jeonghan keyword requirement; duplicate suppression and existing collection safety rules remain active. Total configured accounts: 33 (includes the existing disabled account).

Regression: update the existing configuration count and add a Settings.load -> XCollector._filter_relevant test proving keyword-free and empty/media-caption-style posts from haniwadda survive while an unconfigured author is rejected. This test fails without the new source. JSON structure, count and unique handles validated in orchestration; Python validation delegated to exact-head CI because no local execution tool is available.

No dependency, credentials, hosting, cursor, or state changes. No external implementation is needed for this configuration feature. X did not expose the linked status to the read tool, so no claim is made about its content or live retrieval. Source registration does not establish complete live X collection; #157 and the existing X completeness frontier remain separate.

Rollback: revert this PR. Acceptance: exact-head configuration/unit CI, merge after gates pass, then observe canonical runtime loading 33 sources and eventually a real haniwadda update. Do not equate CI success with live collection completeness.

Initial exact-head CI ran 1,312 tests and exposed historical assertions tied to the original primary registry size, plus chronological ordering in the new collector assertion. The additional account now uses the existing priority registry, preserving the primary registry. Combined inventory assertions are updated to 33 total / 32 enabled, and the new regression checks membership independent of sorting. Re-run CI required before merge.
