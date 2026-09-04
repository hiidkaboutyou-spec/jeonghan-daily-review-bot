from __future__ import annotations

from app.source_ledger import SourceLedgerStore, SourceWindowResult, SourceWindowStatus


def _finish(store, source, start, end, status, **kwargs):
    store.finish(SourceWindowResult(
        source_handle=source,
        window_start=start,
        window_end=end,
        status=status,
        **kwargs,
    ))


def test_complete_advances_only_its_source_cursor(tmp_path):
    store = SourceLedgerStore(tmp_path / "state.sqlite3")
    start = "2026-09-04T10:00:00+00:00"
    end = "2026-09-04T11:00:00+00:00"
    store.start_attempt(source_handle="alpha", window_start=start, window_end=end, attempt_id="a1")
    store.start_attempt(source_handle="beta", window_start=start, window_end=end, attempt_id="b1")
    _finish(store, "alpha", start, end, SourceWindowStatus.COMPLETE, retained_count=3)
    _finish(store, "beta", start, end, SourceWindowStatus.UNPROVEN, error_class="NetworkError")

    assert store.cursor("alpha")["complete_through"] == end
    assert store.cursor("alpha")["last_status"] == "complete"
    assert store.cursor("beta")["complete_through"] == ""
    assert store.cursor("beta")["last_status"] == "unproven"


def test_partial_never_advances_existing_complete_cursor(tmp_path):
    store = SourceLedgerStore(tmp_path / "state.sqlite3")
    s1 = "2026-09-04T09:00:00+00:00"
    e1 = "2026-09-04T10:00:00+00:00"
    s2 = e1
    e2 = "2026-09-04T11:00:00+00:00"
    store.start_attempt(source_handle="alpha", window_start=s1, window_end=e1)
    _finish(store, "alpha", s1, e1, SourceWindowStatus.COMPLETE)
    store.start_attempt(source_handle="alpha", window_start=s2, window_end=e2)
    _finish(store, "alpha", s2, e2, SourceWindowStatus.PARTIAL, error_class="XCompletenessError")

    cursor = store.cursor("alpha")
    assert cursor["complete_through"] == e1
    assert cursor["last_status"] == "partial"
    assert cursor["last_error_class"] == "XCompletenessError"


def test_retry_count_is_scoped_to_same_source_window(tmp_path):
    store = SourceLedgerStore(tmp_path / "state.sqlite3")
    start = "2026-09-04T10:00:00+00:00"
    end = "2026-09-04T11:00:00+00:00"
    store.start_attempt(source_handle="alpha", window_start=start, window_end=end, attempt_id="a1")
    _finish(store, "alpha", start, end, SourceWindowStatus.UNPROVEN)
    store.start_attempt(source_handle="alpha", window_start=start, window_end=end, attempt_id="a2")
    _finish(store, "alpha", start, end, SourceWindowStatus.COMPLETE)

    row = store.window("alpha", start, end)
    assert row["attempt_count"] == 2
    assert row["retry_count"] == 1
    assert store.cursor("alpha")["complete_through"] == end


def test_complete_cursor_is_monotonic(tmp_path):
    store = SourceLedgerStore(tmp_path / "state.sqlite3")
    newer_start = "2026-09-04T10:00:00+00:00"
    newer_end = "2026-09-04T11:00:00+00:00"
    older_start = "2026-09-04T08:00:00+00:00"
    older_end = "2026-09-04T09:00:00+00:00"
    for start, end in ((newer_start, newer_end), (older_start, older_end)):
        store.start_attempt(source_handle="alpha", window_start=start, window_end=end)
        _finish(store, "alpha", start, end, SourceWindowStatus.COMPLETE)

    assert store.cursor("alpha")["complete_through"] == newer_end


def test_counts_and_proof_are_preserved(tmp_path):
    store = SourceLedgerStore(tmp_path / "state.sqlite3")
    start = "2026-09-04T10:00:00+00:00"
    end = "2026-09-04T11:00:00+00:00"
    store.start_attempt(source_handle="Alpha", window_start=start, window_end=end, attempt_id="x")
    _finish(
        store,
        "Alpha",
        start,
        end,
        SourceWindowStatus.COMPLETE,
        raw_observation_count=9,
        retained_count=4,
        proof_kind="timeline_exhausted_or_lower_boundary_crossed",
    )
    row = store.window("alpha", start, end)
    assert row["raw_observation_count"] == 9
    assert row["retained_count"] == 4
    assert row["proof_kind"] == "timeline_exhausted_or_lower_boundary_crossed"


def test_source_statuses_are_separate_and_sorted(tmp_path):
    store = SourceLedgerStore(tmp_path / "state.sqlite3")
    start = "2026-09-04T10:00:00+00:00"
    end = "2026-09-04T11:00:00+00:00"
    for source in ("beta", "alpha"):
        store.start_attempt(source_handle=source, window_start=start, window_end=end)
    assert [row["source_handle"] for row in store.source_statuses()] == ["alpha", "beta"]


def test_runtime_hook_is_installed_after_recovery_layer():
    from app.x_completeness import CompleteWindowXCollector
    assert CompleteWindowXCollector.__dict__.get("_source_ledger_installed", False) is True
