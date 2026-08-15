from __future__ import annotations

import os
import threading
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .models import MediaItem, Update, ensure_utc
from .observability import observe

# Realtime timing telemetry is intentionally metadata-only. Extending the central
# allow-list here keeps the existing scrubber authoritative without changing
# Phase 2/3 logging behavior.
try:
    from . import observability as _observability

    _observability._ALLOWED_TAGS.update(
        {
            "detection_method",
            "created_at",
            "detected_at",
            "processing_started_at",
            "private_delivery_at",
            "detection_latency_ms",
            "end_to_end_latency_ms",
            "backfill_recovery",
            "decision_reason",
            "shadow",
        }
    )
except Exception:
    # Observability must never become a runtime dependency for ingestion.
    pass


SUPPORTED_DETECTION_METHODS = frozenset(
    {"fast_poll", "filtered_stream", "scheduled_backfill", "phase3_recovery"}
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_handle(value: str) -> str:
    return str(value or "").strip().lstrip("@").lower()


def realtime_shadow_enabled() -> bool:
    return os.getenv("REALTIME_SHADOW_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DetectedMedia:
    kind: str
    url: str
    preview_url: str = ""
    bitrate: int = 0
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    content_type: str = ""

    def to_media_item(self) -> MediaItem:
        return MediaItem(
            kind=str(self.kind),
            url=str(self.url),
            preview_url=str(self.preview_url),
            bitrate=self.bitrate,
            width=self.width,
            height=self.height,
            duration_ms=self.duration_ms,
            content_type=str(self.content_type),
        )

    @classmethod
    def from_media_item(cls, media: MediaItem) -> "DetectedMedia":
        return cls(
            kind=media.kind,
            url=media.url,
            preview_url=media.preview_url,
            bitrate=media.bitrate,
            width=media.width,
            height=media.height,
            duration_ms=media.duration_ms,
            content_type=media.content_type,
        )


@dataclass(frozen=True, slots=True)
class RealtimeCandidate:
    """Provider-neutral observation of a post identity.

    The candidate is deliberately not an authoritative content object. Provider
    adapters may supply partial metadata; normal retrieval remains responsible
    for authoritative hydration before a future live-delivery promotion.
    """

    source_handle: str
    post_id: str
    created_at: datetime
    detected_at: datetime
    detection_method: str
    post_url: str = ""
    conversation_id: str = ""
    reply_to_id: str = ""
    is_reply: bool = False
    is_repost: bool = False
    media: tuple[DetectedMedia, ...] = ()
    retrieval_attempt_id: str = ""

    def __post_init__(self) -> None:
        handle = _normalized_handle(self.source_handle)
        post_id = str(self.post_id or "").strip()
        method = str(self.detection_method or "").strip()
        if not handle:
            raise ValueError("source_handle is required")
        if not post_id:
            raise ValueError("post_id is required")
        if method not in SUPPORTED_DETECTION_METHODS:
            raise ValueError(f"unsupported detection_method: {method}")
        created = ensure_utc(self.created_at)
        detected = ensure_utc(self.detected_at)
        if detected < created:
            # Clock skew must not yield a negative latency metric.
            detected = created
        object.__setattr__(self, "source_handle", handle)
        object.__setattr__(self, "post_id", post_id)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "detected_at", detected)
        object.__setattr__(self, "detection_method", method)
        object.__setattr__(
            self,
            "post_url",
            str(self.post_url or "").strip() or f"https://x.com/{handle}/status/{post_id}",
        )
        object.__setattr__(
            self,
            "conversation_id",
            str(self.conversation_id or "").strip() or post_id,
        )
        object.__setattr__(self, "reply_to_id", str(self.reply_to_id or "").strip())
        object.__setattr__(self, "retrieval_attempt_id", str(self.retrieval_attempt_id or "").strip())
        object.__setattr__(self, "media", tuple(self.media or ()))

    @property
    def logical_id(self) -> str:
        return self.post_id


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    handle: str
    enabled: bool
    include_replies: bool

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "SourcePolicy":
        return cls(
            handle=_normalized_handle(str(raw.get("handle", ""))),
            enabled=bool(raw.get("enabled", True)),
            include_replies=bool(raw.get("include_replies", True)),
        )


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    accepted: bool
    reason: str
    policy: SourcePolicy | None = None


class SourceAuthorityGate:
    """Applies the existing configured-source policy to fast observations."""

    def __init__(self, sources: Sequence[Mapping[str, object]]):
        policies = [SourcePolicy.from_mapping(source) for source in sources]
        self._policies = {
            policy.handle: policy
            for policy in policies
            if policy.handle and policy.enabled
        }

    @property
    def configured_handles(self) -> frozenset[str]:
        return frozenset(self._policies)

    def decide_candidate(self, candidate: RealtimeCandidate) -> CandidateDecision:
        policy = self._policies.get(_normalized_handle(candidate.source_handle))
        if policy is None:
            return CandidateDecision(False, "external_source")
        if candidate.is_repost:
            return CandidateDecision(False, "repost_excluded", policy)
        if candidate.is_reply and not policy.include_replies:
            return CandidateDecision(False, "reply_excluded", policy)
        return CandidateDecision(True, "configured_source", policy)

    def validate_authoritative_update(
        self,
        candidate: RealtimeCandidate,
        update: Update,
    ) -> None:
        if str(update.id) != candidate.logical_id:
            raise ValueError("authoritative update id does not match detected post id")
        author = _normalized_handle(update.author)
        if author != _normalized_handle(candidate.source_handle):
            raise ValueError("authoritative update author does not match configured source")
        policy = self._policies.get(author)
        if policy is None:
            raise ValueError("authoritative update is not from a configured source")
        if (update.is_reply or update.reply_to_id) and not policy.include_replies:
            raise ValueError("authoritative update violates configured reply policy")


class LogicalUpdateGate:
    """Process-local idempotency gate layered over durable seen/pending state.

    This protects two detector tasks sharing one process. It deliberately does
    not claim cross-process atomic exactly-once semantics; a future independent
    delivering worker will require a shared atomic state/queue decision.
    """

    def __init__(
        self,
        *,
        is_seen: Callable[[str], bool] | None = None,
        is_pending: Callable[[str], bool] | None = None,
    ):
        self._is_seen = is_seen or (lambda _update_id: False)
        self._is_pending = is_pending or (lambda _update_id: False)
        self._lock = threading.Lock()
        self._claimed: set[str] = set()

    def claim(self, update_id: str) -> tuple[bool, str]:
        key = str(update_id)
        with self._lock:
            if self._is_seen(key):
                return False, "already_seen"
            if self._is_pending(key):
                return False, "already_pending"
            if key in self._claimed:
                return False, "already_claimed"
            self._claimed.add(key)
            return True, "claimed"

    def release(self, update_id: str) -> None:
        with self._lock:
            self._claimed.discard(str(update_id))

    def mark_external_observation(self, update_id: str) -> None:
        """Reserve an id already observed by another authoritative path."""
        with self._lock:
            self._claimed.add(str(update_id))


@dataclass(slots=True)
class LatencyTrace:
    post_id: str
    source: str
    created_at: datetime
    detected_at: datetime
    detection_method: str
    retrieval_attempt_id: str = ""
    processing_started_at: datetime | None = None
    private_delivery_at: datetime | None = None
    backfill_recovery: bool = False

    @property
    def detection_latency_ms(self) -> int:
        delta = ensure_utc(self.detected_at) - ensure_utc(self.created_at)
        return max(0, int(delta.total_seconds() * 1000))

    @property
    def end_to_end_latency_ms(self) -> int | None:
        if self.private_delivery_at is None:
            return None
        delta = ensure_utc(self.private_delivery_at) - ensure_utc(self.created_at)
        return max(0, int(delta.total_seconds() * 1000))

    def mark_processing_started(self, at: datetime | None = None) -> None:
        self.processing_started_at = ensure_utc(at or _utc_now())
        observe(
            "realtime_latency",
            stage="realtime",
            status="processing",
            source=self.source,
            post_id=self.post_id,
            update_id=self.post_id,
            retrieval_attempt_id=self.retrieval_attempt_id,
            detection_method=self.detection_method,
            created_at=ensure_utc(self.created_at).isoformat(),
            detected_at=ensure_utc(self.detected_at).isoformat(),
            processing_started_at=self.processing_started_at.isoformat(),
            detection_latency_ms=self.detection_latency_ms,
            backfill_recovery=self.backfill_recovery,
            shadow=True,
        )

    def mark_private_delivery(self, receipt_id: int, at: datetime | None = None) -> None:
        if int(receipt_id or 0) <= 0:
            raise ValueError("a real Telegram receipt id is required")
        self.private_delivery_at = ensure_utc(at or _utc_now())
        observe(
            "realtime_latency",
            stage="realtime",
            status="private_delivery_observed",
            source=self.source,
            post_id=self.post_id,
            update_id=self.post_id,
            retrieval_attempt_id=self.retrieval_attempt_id,
            delivery_receipt_id=int(receipt_id),
            detection_method=self.detection_method,
            created_at=ensure_utc(self.created_at).isoformat(),
            detected_at=ensure_utc(self.detected_at).isoformat(),
            private_delivery_at=self.private_delivery_at.isoformat(),
            detection_latency_ms=self.detection_latency_ms,
            end_to_end_latency_ms=self.end_to_end_latency_ms,
            backfill_recovery=self.backfill_recovery,
            shadow=True,
        )


@dataclass(slots=True)
class ShadowObservation:
    candidate: RealtimeCandidate
    decision: CandidateDecision
    claimed: bool = False
    hydrated: Update | None = None
    trace: LatencyTrace | None = None
    hydration_error: str = ""


@dataclass(slots=True)
class ShadowRunSummary:
    observed: int = 0
    accepted: int = 0
    duplicate: int = 0
    rejected: int = 0
    hydrated: int = 0
    deferred_to_backfill: int = 0


class FastDetector(Protocol):
    async def candidates(self) -> AsyncIterator[RealtimeCandidate]:
        ...


Hydrator = Callable[[RealtimeCandidate], Awaitable[Update]]


class ShadowRealtimeIngestor:
    """Non-delivering fast-ingest harness.

    Shadow mode can normalize, policy-check, dedupe, hydrate and measure a fast
    detector. It has no queue, cursor, completeness or Telegram delivery API.
    The scheduled Phase 3 path therefore remains the completeness authority.
    """

    def __init__(
        self,
        *,
        sources: Sequence[Mapping[str, object]],
        logical_gate: LogicalUpdateGate | None = None,
        enabled: bool | None = None,
    ):
        self.authority = SourceAuthorityGate(sources)
        self.logical_gate = logical_gate or LogicalUpdateGate()
        self.enabled = realtime_shadow_enabled() if enabled is None else bool(enabled)

    def inspect(self, candidate: RealtimeCandidate) -> ShadowObservation:
        if not self.enabled:
            return ShadowObservation(
                candidate=candidate,
                decision=CandidateDecision(False, "shadow_disabled"),
            )

        decision = self.authority.decide_candidate(candidate)
        if not decision.accepted:
            observe(
                "realtime_candidate",
                stage="realtime",
                status="rejected",
                source=candidate.source_handle,
                post_id=candidate.post_id,
                update_id=candidate.post_id,
                retrieval_attempt_id=candidate.retrieval_attempt_id,
                detection_method=candidate.detection_method,
                decision_reason=decision.reason,
                shadow=True,
            )
            return ShadowObservation(candidate=candidate, decision=decision)

        claimed, reason = self.logical_gate.claim(candidate.logical_id)
        if not claimed:
            observe(
                "realtime_candidate",
                stage="realtime",
                status="duplicate",
                source=candidate.source_handle,
                post_id=candidate.post_id,
                update_id=candidate.post_id,
                retrieval_attempt_id=candidate.retrieval_attempt_id,
                detection_method=candidate.detection_method,
                decision_reason=reason,
                shadow=True,
            )
            return ShadowObservation(
                candidate=candidate,
                decision=CandidateDecision(False, reason, decision.policy),
            )

        trace = LatencyTrace(
            post_id=candidate.post_id,
            source=candidate.source_handle,
            created_at=candidate.created_at,
            detected_at=candidate.detected_at,
            detection_method=candidate.detection_method,
            retrieval_attempt_id=candidate.retrieval_attempt_id,
            backfill_recovery=candidate.detection_method in {"scheduled_backfill", "phase3_recovery"},
        )
        observe(
            "realtime_candidate",
            stage="realtime",
            status="shadow_detected",
            source=candidate.source_handle,
            post_id=candidate.post_id,
            update_id=candidate.post_id,
            retrieval_attempt_id=candidate.retrieval_attempt_id,
            detection_method=candidate.detection_method,
            created_at=candidate.created_at.isoformat(),
            detected_at=candidate.detected_at.isoformat(),
            detection_latency_ms=trace.detection_latency_ms,
            decision_reason=decision.reason,
            shadow=True,
        )
        return ShadowObservation(
            candidate=candidate,
            decision=decision,
            claimed=True,
            trace=trace,
        )

    def hydrate(self, observation: ShadowObservation, authoritative: Update) -> Update:
        if not observation.claimed:
            raise ValueError("candidate must hold a logical claim before hydration")
        self.authority.validate_authoritative_update(observation.candidate, authoritative)

        # Authoritative retrieval wins. Candidate metadata is only allowed to
        # fill fields that the authoritative result did not have yet.
        if authoritative.media:
            media = authoritative.media
        else:
            media = [item.to_media_item() for item in observation.candidate.media]

        enriched = Update(
            id=authoritative.id,
            url=authoritative.url or observation.candidate.post_url,
            author=authoritative.author,
            author_name=authoritative.author_name,
            text=authoritative.text,
            created_at=authoritative.created_at,
            conversation_id=authoritative.conversation_id or observation.candidate.conversation_id,
            reply_to_id=authoritative.reply_to_id or observation.candidate.reply_to_id,
            quoted_id=authoritative.quoted_id,
            quoted_text=authoritative.quoted_text,
            quoted_author=authoritative.quoted_author,
            quoted_media=list(authoritative.quoted_media),
            lang=authoritative.lang,
            media=media,
            category=authoritative.category,
            event_key=authoritative.event_key,
            event_title=authoritative.event_title,
            source_priority=authoritative.source_priority,
            is_reply=authoritative.is_reply or observation.candidate.is_reply,
            raw_query=authoritative.raw_query,
        )
        observation.hydrated = enriched
        if observation.trace is not None:
            observation.trace.mark_processing_started()
        return enriched

    async def run_shadow(
        self,
        detector: FastDetector,
        hydrate: Hydrator,
    ) -> ShadowRunSummary:
        summary = ShadowRunSummary()
        try:
            async for candidate in detector.candidates():
                summary.observed += 1
                observation = self.inspect(candidate)
                if not observation.claimed:
                    if observation.decision.reason in {
                        "already_seen",
                        "already_pending",
                        "already_claimed",
                    }:
                        summary.duplicate += 1
                    else:
                        summary.rejected += 1
                    continue
                summary.accepted += 1
                try:
                    authoritative = await hydrate(candidate)
                    self.hydrate(observation, authoritative)
                    summary.hydrated += 1
                except Exception as exc:
                    observation.hydration_error = type(exc).__name__
                    self.logical_gate.release(candidate.logical_id)
                    summary.deferred_to_backfill += 1
                    observe(
                        "realtime_candidate",
                        stage="realtime",
                        status="deferred_to_backfill",
                        source=candidate.source_handle,
                        post_id=candidate.post_id,
                        update_id=candidate.post_id,
                        retrieval_attempt_id=candidate.retrieval_attempt_id,
                        detection_method=candidate.detection_method,
                        error_class=type(exc).__name__,
                        decision_reason="authoritative_hydration_failed",
                        shadow=True,
                    )
        except Exception as exc:
            summary.deferred_to_backfill += 1
            observe(
                "realtime_detector",
                stage="realtime",
                status="deferred_to_backfill",
                error_class=type(exc).__name__,
                decision_reason="detector_failed",
                shadow=True,
            )
        return summary
