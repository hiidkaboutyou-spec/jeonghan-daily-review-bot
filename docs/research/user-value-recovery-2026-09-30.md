# User-value recovery: Hani Inbox (2026-09-30)

## Why this stage exists

The project is technically robust but still fails the owner's primary product test: it does not replace the old manual workflow.

Verified production evidence already recorded in this repository shows that a due Daily window attempted all 31 active X sources but completed 0 of them through the authenticated provider. The public profile recovery path still recovered useful partial items, while the full-success cursor correctly remained held. The later authenticated runner matrix returned HTTP 403 on Ubuntu, macOS and Windows. More architecture hardening cannot make the assistant useful if the input layer stays incomplete.

At the same time, the post-ingestion pipeline is already valuable: grouping, media preparation, Persian translation/channel-style generation, private Telegram delivery, archive storage and draft rewrite actions work on an existing `Update`. The missing UX bridge is that an owner-pasted X status URL is currently treated as an unknown command.

## Deep-research decision

### Immediate product bridge: X-owned single-status syndication

Current `yt-dlp` still implements X's public `cdn.syndication.twimg.com/tweet-result` endpoint as its syndication fallback. Other current open-source clients document the same endpoint as unauthenticated structured JSON and expose text, author, timestamp, reply/quote context and media metadata.

This PR uses that endpoint **only for a public post URL explicitly sent by the private admin**. It never accepts a user-supplied fetch hostname: the parser extracts only the numeric status ID and requests a fixed X-owned endpoint. The returned ID and author are revalidated before conversion to the repository's `Update` model.

This is intentionally not timeline authority. It does not change source-completeness evidence, checkpoints, recovery state, `last_auto_run`, or any success cursor.

### Manual-text bridge

A second fast lane lets the owner paste Korean/Japanese/English/raw source text after pressing **Quick Inbox**. The input becomes an ephemeral manual `Update` and goes through the same existing translation/style/archive/draft pipeline. This covers Weverse, Instagram, interviews, captions and other material that was never an X-timeline problem.

### Automatic collection candidates researched

- **FxEmbed / FxTwitter API v2** is the strongest current no-secret research candidate for a shadow timeline experiment. Its current documentation exposes profile statuses, search and cursor pagination, the project is MIT-licensed and self-hostable, and the public API documents a bounded rate limit. Because it is a third-party data path and current issue reports show some response/schema edge cases, it must first be tested as a no-delivery/no-state shadow provider before any production promotion.
- **RSSHub X routes** are not selected as the next production provider. Current 2026 issues document 401/403 token-pool failures and stale guest-mode results, which is exactly the class of silent incompleteness this project must avoid.
- **Nitter** is not selected as a production dependency. Its repository entered an unstable legal/operational period in August-September 2026 and was archived, so it is not a sensible foundation for a private assistant that must stay dependable.
- **Official X API** remains the cleanest authority path if the owner later chooses scoped credentials and a budget, but this repository currently has neither authorization nor credentials for that change.

## UX after this PR

1. Share/paste an X status link directly into the private bot.
2. The bot fetches only that exact public post, then uses the existing media + translation + ChannelStyle + archive + rewrite pipeline.
3. For non-X material, tap **✍️ ورودی سریع** and paste the source text.
4. Automatic Daily monitoring continues independently and remains honest about partial X coverage.

## Safety / rollback

- no new secret;
- no new dependency;
- no new external recipient for private text;
- admin-only Telegram boundary remains unchanged;
- at most 8 shared status URLs are processed from one message;
- fixed-host fetch prevents arbitrary URL/SSRF behavior;
- returned status ID/author are validated;
- no automatic public publishing;
- no automatic X cursor/state authority change;
- rollback is a normal revert of this focused PR; no state migration is required.

## Next automatic-monitoring gate

Do not call automatic monitoring restored until a no-delivery shadow run proves complete, paginated due-window retrieval over all configured sources and then survives an exact-head/real-main promotion gate. FxTwitter v2 is worth benchmarking for that role; it is not silently promoted by this PR.


## Research sources

Primary implementation references checked on 2026-09-30:

- yt-dlp current X extractor, including its public syndication fallback and token generation:
  https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/twitter.py
- FxEmbed API v2 overview (profiles, statuses, search, pagination and public rate limit):
  https://github.com/FxEmbed/FxEmbed/blob/main/docs/src/content/docs/api/introduction.mdx
- FxEmbed repository / self-hosting / MIT license:
  https://github.com/FxEmbed/FxEmbed
- FxTwitter profile-status edge-case report kept as a reason for shadow validation before promotion:
  https://github.com/FxEmbed/FxEmbed/issues/2011
- RSSHub 2026 X auth-token failure/stale guest-mode evidence:
  https://github.com/DIYgod/RSSHub/issues/23164
  https://github.com/DIYgod/RSSHub/issues/23255
- Nitter current README/legal-status notice:
  https://github.com/zedeus/nitter/blob/master/README.md


## Follow-up: editorial-order UX gap

Owner feedback after Hani Inbox was explicit: updates still felt disordered. The missing product behavior was not raw chronological sorting alone. The private assistant also needed to make four things visible at review time:

1. which item should be posted first;
2. which items belong to the same event;
3. when each item/event originally appeared;
4. where a new source is probably covering an earlier event rather than introducing a separate topic.

The runtime already had deterministic oldest-to-newest grouping plus shadow Event Fusion evidence, but the Telegram surface hid most of that information. The pending inbox also defaulted to Phase 5 source-first navigation, which is useful for coverage auditing but conflicts with the owner's primary editorial question: **what do I post next?**

### Product decision

- Keep source-first review as an explicit coverage/audit view.
- Make the normal pending inbox editorial-first and chronological.
- Add a private **نقشهٔ انتشار** before multi-update deliveries.
- Replace the old generic batch-status message with one private **نقشهٔ انتشار** showing original local time, event/topic label, source preview, part count, and conservative relation to an earlier group.
- Use Event Fusion only as advisory metadata for “same event / probably related”; do not merge canonical delivery groups or alter retrieval/state authority.
- Never add navigation metadata to the saved channel caption; copy/rewrite output remains clean.

This change is intentionally UX-only. It does not modify collection completeness, cursor authority, translation, media identity, seen-state, or public publishing.
