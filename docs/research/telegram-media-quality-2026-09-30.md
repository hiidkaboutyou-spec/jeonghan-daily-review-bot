# Telegram media quality research and implementation — 2026-09-30

Issue: #153

## User outcome

The private Hani assistant should deliver the best practical X media quality without inventing resolution:

- video priority: real 1080p → real 720p → highest remaining source quality;
- portrait and landscape are treated consistently by their shorter edge;
- no upscaling of a lower-resolution source;
- photo retrieval explicitly requests the X CDN order: orig → 4096x4096 → supplied URL → large → medium → small;
- Telegram delivery remains bounded by the official Bot API upload constraints.

## Baseline/root cause

Before this change the media layer already had strong fallbacks, but its quality policy was implicit:

- X/twscrape video variants were sorted only by bitrate;
- yt-dlp preferred compatible codecs/containers but did not express a 1080p/720p target;
- the FFmpeg compatibility transcode capped width at 1280, so a landscape 1920x1080 source could be reduced to 1280x720 whenever a transcode was needed;
- X photo fallback could accept a supplied lower-size CDN URL before attempting `orig` when that URL was not already original;\n- Telegram video file_ids were cached under the previous normalization policy, so a code-only improvement could continue replaying old lower-quality cached uploads.

That combination explains why a valid source could still arrive below the user's preferred quality even though "best" media retrieval existed.

## External research

### Telegram Bot API — authoritative platform constraint

Official Bot API documentation currently states that bots can send video/document files up to 50 MB. The repository therefore keeps the existing 44 MiB internal safety ceiling so multipart overhead and API behavior have margin.

Decision: keep the current Bot API architecture. Do not pretend to support the multi-GB behavior publicly advertised by some downloader bots without a separate upload/client architecture.

Source:
- https://core.telegram.org/bots/api

### yt-dlp — adopted quality semantics

yt-dlp documents:

- `res` as the smaller video dimension, which correctly handles portrait video;
- format sorting with preferred values such as `res:720`;
- codec/container sorting and bestvideo+bestaudio merging.

Decision: adapt yt-dlp's resolution semantics and keep the existing H.264/AAC/MP4 compatibility preference. No fork or copied downloader is needed.

Source:
- https://github.com/yt-dlp/yt-dlp/blob/master/README.md

### gallery-dl — retain existing photo policy

The upstream gallery-dl Twitter/X configuration uses:
`["orig", "4096x4096", "large", "medium", "small"]`.

Decision: make the order explicit and source-independent: request `orig` first even when an incoming X URL names `small`/`medium`/`large`, then 4096x4096, then the supplied URL and lower fallbacks.

Source:
- https://github.com/mikf/gallery-dl/blob/master/docs/gallery-dl.conf

### @twittervid_bot — UX inspiration only

The public Telegram channel for `@twittervid_bot` advertises:

- mixed photo + video tweet support;
- alternate-quality video downloads;
- X/vxTwitter handling;
- large-video features in its own infrastructure.

The bot is not an auditable dependency and its implementation is not copied. The relevant idea for Hani is the explicit quality ladder and graceful fallback.

Public evidence:
- https://t.me/s/twittervid

### Comparable open-source downloaders

Reviewed as implementation references, not dependencies:

- `driversti/ytdlp-telegram`: exposes 480p/720p/1080p/best choices and a separate large-file server.
- `atex-ovi/tg-dl-bot`: yt-dlp-based quality selection up to 1080p and explicitly documents the normal Telegram 50 MB limit.
- `kevorteg/telegram-media-downloader`: yt-dlp + gallery-dl fallback and size-aware FFmpeg compression.
- `Teamhapp/telegram-twitter-video-bot`: simple Python X downloader with configurable quality.

Decision: adapt the quality-selection pattern only. Do not add a second framework, queue, database, downloader service, or public-bot dependency.

## Implementation

### Source variant ranking

New `app/media_quality.py` centralizes the quality contract:

1. parse dimensions embedded in X CDN URLs when available;
2. use the short edge as the orientation-independent resolution;
3. rank exact 1080p first;
4. rank exact 720p second;
5. otherwise rank the highest remaining real resolution, then bitrate;
6. keep unknown-resolution variants behind known variants rather than inventing dimensions.

`app/x_client.py` now records parsed width/height on `MediaItem` and applies this ranking instead of bitrate-only selection.

### yt-dlp fallback

The existing trusted-host yt-dlp fallback keeps its compatibility-focused format selector and adds a resolution-first sort:
`res:1080, codec:avc:m4a, ext:mp4:m4a, br`.

This uses yt-dlp's documented short-edge resolution model and preserves broad fallback when preferred formats do not exist.

### FFmpeg normalization/compression

Compatibility normalization now allows up to 1920x1920 bounds without upscaling, preserving a real 1920x1080 or 1080x1920 source when it needs H.264/AAC conversion.

Oversize compression now tries:
1. 1080-class preservation;
2. 720-class reduction;
3. lower bounded fallback.

Resolution is reduced only when needed to fit the existing safe Telegram upload budget.

### Cache invalidation

Video file-id cache version changes from `telegram-ios-video-v3` to `telegram-hq-video-v4`. This prevents the improved policy from immediately replaying lower-quality uploads cached under the old behavior.

Photo cache behavior is unchanged because the current photo source path already uses the upstream orig-first quality policy.

## Safety / non-goals

- No source is upscaled.
- No Telegram/X/Gemini secret behavior changes.
- No new network host or dependency is introduced.
- No state schema migration is needed; only video cache keys rotate naturally.
- Mixed photo/video Telegram albums remain unchanged.
- Exact original-photo-as-document delivery is intentionally not added here because it would materially change album UX; the source bytes remain orig-first before Telegram's normal photo handling.
- Multi-GB media delivery like @twittervid_bot is not claimed.

## Validation ladder

Required before merge:

1. focused `test_media_quality_policy.py`;
2. existing `test_media.py` and `test_video_delivery_e2e.py`;
3. repository baseline;
4. exact-head PR CI;
5. after merge, main CI/runtime;
6. owner-visible confirmation from a real X post whose source has known 1080p/720p media.

## Rollback

Revert the PR. The old video cache keys remain harmless historical rows and will simply become reachable again if the cache-version constant is reverted. No destructive migration is performed.

## Completion boundary

Green tests can prove ranking/normalization behavior, but they cannot prove perceived Telegram quality for a real X post. Final owner-visible acceptance requires a real delivered sample after merge.
