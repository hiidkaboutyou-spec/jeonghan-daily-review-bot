"""Compatibility guard for additive Event Fusion durable metadata.

Event Fusion is an additive namespace inside the already production-verified
StateStore schema. It must not change the top-level durable-state schema contract.
This module captures that contract before Event Fusion is imported, restores it
afterward, and canonicalizes bounded Event metadata written by the shadow layer.
"""
from __future__ import annotations

from typing import Any

from . import state as _state_module

BASE_STATE_SCHEMA_VERSION = int(_state_module.SCHEMA_VERSION)


def install(event_fusion: Any) -> None:
    """Restore the existing StateStore schema and canonicalize Event metadata."""
    _state_module.SCHEMA_VERSION = BASE_STATE_SCHEMA_VERSION

    current_membership = event_fusion._membership
    if not getattr(current_membership, "_event_state_compat_installed", False):
        def membership(event_state, event_id, update_id, match):
            current_membership(event_state, event_id, update_id, match)
            row = event_state.get("memberships", {}).get(str(update_id))
            if isinstance(row, dict):
                row["matching_signals"] = sorted(
                    {str(item) for item in row.get("matching_signals", []) if str(item)}
                )
                row["conflicts"] = sorted(
                    {str(item) for item in row.get("conflicts", []) if str(item)}
                )

        membership._event_state_compat_installed = True
        event_fusion._membership = membership

    current_record = event_fusion._record
    if not getattr(current_record, "_event_state_compat_installed", False):
        def record(event_state, update_id, decision, **kwargs):
            current_record(event_state, update_id, decision, **kwargs)
            decisions = event_state.get("decisions", [])
            if decisions and isinstance(decisions[-1], dict):
                row = decisions[-1]
                row["matching_signals"] = sorted(
                    {str(item) for item in row.get("matching_signals", []) if str(item)}
                )
                row["conflicts"] = sorted(
                    {str(item) for item in row.get("conflicts", []) if str(item)}
                )

        record._event_state_compat_installed = True
        event_fusion._record = record
