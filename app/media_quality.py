from __future__ import annotations

import re

_X_VIDEO_DIMENSIONS_RE = re.compile(r"/(?:vid|video)/(?:[^/]+/)*?(\d{2,5})x(\d{2,5})/", re.I)


def x_variant_dimensions(url: str) -> tuple[int, int]:
    """Extract source dimensions embedded in X CDN video URLs when present."""
    match = _X_VIDEO_DIMENSIONS_RE.search(str(url or ""))
    if not match:
        return 0, 0
    try:
        width = int(match.group(1))
        height = int(match.group(2))
    except (TypeError, ValueError):
        return 0, 0
    if width <= 0 or height <= 0:
        return 0, 0
    return width, height


def short_edge_resolution(width: int, height: int) -> int:
    """Return orientation-independent resolution (yt-dlp's documented res semantics)."""
    try:
        width = int(width or 0)
        height = int(height or 0)
    except (TypeError, ValueError):
        return 0
    if width <= 0 or height <= 0:
        return 0
    return min(width, height)


def quality_rank(width: int, height: int, bitrate: int = 0) -> tuple[int, int, int]:
    """Rank source variants as 1080p, then 720p, then highest remaining real quality.

    Resolution uses the shorter edge so portrait 1080x1920 and landscape
    1920x1080 are both 1080p. Unknown dimensions stay behind known variants and
    fall back to bitrate. The policy never invents/upscales a resolution.
    """
    resolution = short_edge_resolution(width, height)
    try:
        bitrate = max(0, int(bitrate or 0))
    except (TypeError, ValueError):
        bitrate = 0
    if resolution == 1080:
        tier = 3
    elif resolution == 720:
        tier = 2
    elif resolution > 0:
        tier = 1
    else:
        tier = 0
    return tier, resolution, bitrate


def ytdlp_quality_sort() -> list[str]:
    """Prefer 1080p/720p-compatible sources while keeping best-source fallback.

    yt-dlp documents `res` as the smaller dimension, so this also behaves
    correctly for portrait video. `res:1080` chooses the largest resolution
    not exceeding 1080 and the smallest one above it if no <=1080 format exists.
    """
    return ["res:1080", "codec:avc:m4a", "ext:mp4:m4a", "br"]
