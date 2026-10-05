# @anshelhan priority full-feed source

Run: `2026-10-05T12:37+03:30-anshelhan-coverage`; base: `590d812618b450f70d366bae41a8d13f00d3fc09`.

## Problem and acceptance

The owner explicitly named `@anshelhan` as a priority Daily ingestion source and supplied `https://x.com/anshelhan/status/2105453246903210472?s=46` as source evidence. Fresh inspection of `main` found `@haniwadda` enabled in `config/jeonghan_priority_x_sources.json`, but no `@anshelhan` entry anywhere in config. GitHub searches found no open PR, issue, or branch containing `anshelhan`, and the repository has no lock/lease mechanism that can guarantee exclusion of another concurrent writer.

Acceptance for this bounded change:

- `Settings.load` yields exactly one enabled `anshelhan` source with priority 10, replies included, and `full_feed` mode;
- the concrete `Settings.load -> XCollector._filter_relevant` boundary retains keyword-free and empty/media-caption posts from that source, while rejecting an unconfigured source;
- config validation, focused tests, repository baseline, exact-head CI, and post-merge runtime evidence are reported separately.

## Evidence and implementation

Before implementation, the focused regression failed with `33 != 34` configured sources and zero `anshelhan` matches. The implementation adds one entry to the existing priority registry. It reuses the existing source-mode, retry/fallback, timeout, deduplication, chronological ordering, cursor retention, and checkpoint paths; no dependency, credential, hosted service, or second state authority is introduced.

Current production evidence on the base deployment is incomplete by design: authenticated X cookies are missing, public recovery reported 59 retained updates from 31/31 selected sources, and the runtime emitted `complete=false`, `partial=true`, `cursor_advanced=false`. Because `anshelhan` was absent from config, this is a coverage gap—not evidence that the source had no updates. The linked X status is identification evidence only; its contents were not independently asserted.

## Risk, rollback, and next step

The added full-feed account can increase review volume because it intentionally bypasses the Jeonghan keyword filter. Existing deduplication and private-review delivery remain in force. Rollback is a revert of the config entry plus its regression/count assertion.

After local verification, publish the isolated branch and require exact-head CI. If merged, observe a canonical production scan for an explicit `anshelhan` attempt/result and confirm partial recovery still retains the success cursor. A successful workflow or source registration alone must not be described as complete live coverage; a real update requires its own queue/delivery receipt evidence.
