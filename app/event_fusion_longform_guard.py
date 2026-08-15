"""Conservative guard for long-form Event shadow grouping.

Before Segment/Timeline Fusion exists, a common Live/interview/episode container is
not enough evidence that two source Updates describe the same moment.
"""
from __future__ import annotations

from . import event_fusion as _event_fusion

_LONG_FORM_TYPES = {"live", "interview", "going_seventeen", "variety", "reality"}
_STRONG = {
    "direct_reply_relation",
    "same_conversation",
    "shared_quoted_reference",
    "shared_external_reference",
}


def _install() -> None:
    current = _event_fusion.match_fingerprints
    if getattr(current, "_longform_event_guard_installed", False):
        return

    def guarded(left, right):
        result = current(left, right)
        same_container_type = (
            left.event_type == right.event_type
            and left.event_type in _LONG_FORM_TYPES
        )
        has_strong_reference = any(
            signal in _STRONG for signal in result.matching_signals
        )
        if (
            same_container_type
            and not has_strong_reference
            and result.decision in {"confident_same_event", "probable_same_event"}
        ):
            confidence = round(max(0.0, result.confidence - 0.20), 3)
            conflicts = tuple(
                sorted(set(result.conflicts) | {"long_form_moment_ambiguous"})
            )
            decision = "ambiguous" if confidence >= 0.45 else "separate_event"
            return _event_fusion.EventCandidate(
                result.left_update_id,
                result.right_update_id,
                confidence,
                result.matching_signals,
                conflicts,
                decision,
            )
        return result

    guarded._longform_event_guard_installed = True
    _event_fusion.match_fingerprints = guarded


_install()
