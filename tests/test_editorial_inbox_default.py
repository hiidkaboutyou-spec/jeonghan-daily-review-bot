from __future__ import annotations

import asyncio

from app import source_first_runtime


def test_pending_inbox_prefers_editorial_order_but_keeps_other_filters_legacy():
    calls = []

    class DummyApplication:
        def __init__(self, settings):
            calls.append(("init", settings))

        def show_inbox(self, *, status="pending", page=0, message_id=None):
            calls.append(("legacy", status, page, message_id))

        async def handle_callback(self, callback):
            calls.append(("callback", callback))

        async def handle_draft_action(self, action, draft_id, message_id):
            calls.append(("draft", action, draft_id, message_id))

        async def deliver_updates(self, updates, *, force):
            calls.append(("deliver", force))

    source_first_runtime.install(DummyApplication)
    app = object.__new__(DummyApplication)
    app.show_editorial_inbox = lambda *, message_id=None: calls.append(
        ("editorial", message_id)
    )

    app.show_inbox(status="pending", page=0, message_id=44)
    app.show_inbox(status="ready", page=2, message_id=45)

    assert ("editorial", 44) in calls
    assert ("legacy", "ready", 2, 45) in calls
    assert not any(
        item[0] == "legacy" and len(item) > 1 and item[1] == "pending"
        for item in calls
    )
