"""Shadow rollout: never changes production health or Telegram decisions."""
from __future__ import annotations

from contextvars import ContextVar
import logging
import os

from .completeness_engine import CompletenessEngine, utc
from .completeness_evidence import TraversalEvidence, active_evidence
from .source_ledger_runtime import _ledger_for
from .x_client import XCollector, normalize_handle
from .x_completeness import CompleteWindowXCollector

_run: ContextVar[tuple | None] = ContextVar("completeness_run", default=None)


def publish_report(collector, engine, run_id):
    report = engine.report(run_id)
    collector.last_completeness_report = report
    logging.getLogger(__name__).info(
        "Completeness shadow run=%s configured=%s attempted=%s complete=%s healthy=%s",
        run_id, report["configured"], report["attempted"], report["complete"], report["healthy"],
    )


def enabled() -> bool:
    mode = os.environ.get("COMPLETENESS_ENGINE_MODE", "shadow")
    if mode not in ("shadow", "disabled"):
        raise ValueError("COMPLETENESS_ENGINE_MODE must be shadow or disabled")
    return mode == "shadow"


def install():
    if getattr(XCollector.collect_window, "_completeness_engine", False):
        return
    original_window = XCollector.collect_window
    original_timeline = CompleteWindowXCollector._collect_source_timeline

    async def window(self, start, end, *args, **kwargs):
        if not enabled():
            self.last_completeness_report = {"mode": "disabled", "healthy": False}
            return await original_window(self, start, end, *args, **kwargs)
        engine = CompletenessEngine(_ledger_for(self))
        run_id = engine.plan(self.sources, start, end)
        token = _run.set((self, engine, run_id))
        try:
            return await original_window(self, start, end, *args, **kwargs)
        finally:
            _run.reset(token)
            engine.close_run(run_id)
            publish_report(self, engine, run_id)

    async def timeline(self, handle, start, end, *, limit, include_replies):
        if not enabled():
            return await original_timeline(self, handle, start, end, limit=limit, include_replies=include_replies)
        current = _run.get()
        standalone = current is None or current[0] is not self
        if standalone:
            engine = CompletenessEngine(_ledger_for(self))
            run_id = engine.plan([{"handle": handle}], start, end)
        else:
            _, engine, run_id = current
        attempt_id = engine.start(run_id, handle)
        evidence = TraversalEvidence(
            source_handle=normalize_handle(handle).casefold(),
            window_start=utc(start),
            window_end=utc(end),
            checkpoint=lambda value: engine.checkpoint(attempt_id, value),
            link_observation=lambda post_id: engine.link_observation(attempt_id, post_id),
        )
        token = active_evidence.set(evidence)
        retained = 0
        error = ""
        try:
            result = await original_timeline(self, handle, start, end, limit=limit, include_replies=include_replies)
            retained = len(result)
            return result
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            active_evidence.reset(token)
            try:
                engine.finish(attempt_id, evidence, retained, error)
            finally:
                if standalone:
                    engine.close_run(run_id)
                    publish_report(self, engine, run_id)

    window._completeness_engine = True
    # Keep the Phase 2 marker: reinstallation must not stack ledger wrappers.
    timeline._source_ledger_hook = True
    XCollector.collect_window = window
    XCollector._collect_source_timeline = timeline
    CompleteWindowXCollector._collect_source_timeline = timeline


install()
