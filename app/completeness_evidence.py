"""Attempt-local raw traversal evidence; never derived from editorial filtering."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class TraversalEvidence:
    source_handle: str = ""
    window_start: str = ""
    window_end: str = ""
    pages: int = 0
    raw_count: int = 0
    observation_ids: set[str] = field(default_factory=set)
    expected_window_ids: set[str] = field(default_factory=set)
    provider_cursor: str = ""
    exhausted: bool = False
    valid_response: bool = False
    resumed: bool = False
    lower_boundary: bool = False
    lower_boundary_proven: bool = False
    timeline_order_valid: bool = True
    newest_top_level_at: str = ""
    oldest_top_level_at: str = ""
    previous_page_oldest_at: str = ""
    top_level_ids_seen: set[str] = field(default_factory=set)
    checkpoint: Callable | None = None
    link_observation: Callable | None = None


active_evidence: ContextVar[TraversalEvidence | None] = ContextVar(
    "completeness_evidence", default=None
)


def record_page(*, count: int, cursor: str | None, valid: bool) -> None:
    evidence = active_evidence.get()
    if evidence is not None:
        evidence.valid_response = (
            valid if evidence.pages == 0 else evidence.valid_response and valid
        )
        evidence.pages += 1
        evidence.raw_count += count
        evidence.provider_cursor = str(cursor or "")[:4096]
        if evidence.checkpoint is not None:
            evidence.checkpoint(evidence)


def record_observation(post_id: str) -> None:
    evidence = active_evidence.get()
    if evidence is not None:
        evidence.observation_ids.add(str(post_id))
        if evidence.link_observation is not None:
            evidence.link_observation(str(post_id))
