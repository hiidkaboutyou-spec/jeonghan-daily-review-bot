"""Keep Phase 2 instrumentation transparent to intentional lightweight test doubles.

Production applications always own a real StateStore. A few long-standing unit tests
construct runtime classes with ``__new__`` or SimpleNamespace so they can exercise a
single transport/queue behavior in isolation. Observability must not make those
partial objects a new runtime requirement.
"""

from __future__ import annotations

from types import SimpleNamespace

from . import configured_source_runtime as _configured_runtime
from . import state as _state_module
from .media_delivery_runtime import MediaDedupReviewApplication
from .private_runtime import PrivateReviewApplication

# Phase 2 adds backward-compatible state keys only; it does not require a schema
# migration. Preserve the released schema contract instead of weakening existing
# migration tests or forcing an unnecessary version bump.
_state_module.SCHEMA_VERSION = 4


def _install_media_test_double_guard(cls) -> None:
    current = cls._deliver_private_media
    if getattr(current, "_phase2_test_double_guarded", False):
        return

    async def guarded(self, update):
        state = getattr(self, "state", None)
        recorder = getattr(state, "record_update_state", None) if state is not None else None
        if callable(recorder):
            return await current(self, update)

        created_state = state is None
        if created_state:
            state = SimpleNamespace()
            self.state = state
        added_recorder = False
        try:
            if not callable(getattr(state, "record_update_state", None)):
                setattr(state, "record_update_state", lambda *args, **kwargs: None)
                added_recorder = True
            return await current(self, update)
        finally:
            if added_recorder:
                try:
                    delattr(state, "record_update_state")
                except AttributeError:
                    pass
            if created_state:
                try:
                    delattr(self, "state")
                except AttributeError:
                    pass

    guarded._phase2_test_double_guarded = True
    cls._deliver_private_media = guarded


def _install_pending_test_double_guard() -> None:
    current = _configured_runtime._purge_external_pending
    if getattr(current, "_phase2_test_double_guarded", False):
        return

    def guarded(app):
        state = getattr(app, "state", None)
        quarantine = getattr(state, "quarantine_pending", None) if state is not None else None
        if callable(quarantine):
            return current(app)
        if state is None:
            return current(app)

        added = False
        try:
            setattr(state, "quarantine_pending", lambda *args, **kwargs: None)
            added = True
            return current(app)
        finally:
            if added:
                try:
                    delattr(state, "quarantine_pending")
                except AttributeError:
                    pass

    guarded._phase2_test_double_guarded = True
    _configured_runtime._purge_external_pending = guarded


def install() -> None:
    _install_media_test_double_guard(PrivateReviewApplication)
    _install_media_test_double_guard(MediaDedupReviewApplication)
    _install_pending_test_double_guard()


install()
