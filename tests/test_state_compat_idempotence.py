from types import SimpleNamespace
import unittest

from app import channel_style_rewrite_state_compat as style_compat
from app import forward_ready_state_compat as forward_compat
from app import user_voice_calibration_state_compat as voice_compat


class EventFusion:
    def __init__(self):
        self.prune_calls = 0
        self.sanitize_calls = 0

    def _sanitize_event_state(self, raw):
        self.sanitize_calls += 1
        return dict(raw) if isinstance(raw, dict) else {}

    def _prune(self, state):
        self.prune_calls += 1


class StateCompatInstallIdempotenceTests(unittest.TestCase):
    def test_repeated_compat_installs_do_not_form_recursive_prune_chain(self):
        event = EventFusion()
        style = SimpleNamespace(
            _fresh_style_fields=lambda: {},
            _prune_style=lambda state: state.setdefault("style_pruned", 0) or None,
            MAX_STYLE_RESULTS=10,
            MAX_DIRECT_STYLE_SYMBOL_HISTORY=10,
            MAX_STYLE_EXAMPLES=5,
            DEFAULT_AUTHORITY_ORDER=[],
            STYLE_REWRITE_MODE="shadow",
            DIRECT_STYLE_RULES_VERSION="1",
            DIRECT_STYLE_RULES_MODE="shadow",
        )
        planner = SimpleNamespace(
            _fresh_forward_ready_fields=lambda: {"forward_ready_packages": {}},
            _prune_forward_ready=lambda state: state.setdefault("forward_pruned", 0) or None,
        )
        calibration = SimpleNamespace(
            VOICE_CALIBRATION_VERSION=1,
            VOICE_CALIBRATION_MODE="shadow",
            MAX_CALIBRATION_RECORDS=10,
        )

        # Simulate application startup paths that install compatibility layers
        # more than once, in an interleaved order.
        for _ in range(2):
            style_compat.install(event, style)
            forward_compat.install(event, planner)
            voice_compat.install(event, calibration)

        state = {}
        event._prune(state)
        clean = event._sanitize_event_state({})

        self.assertEqual(event.prune_calls, 1)
        self.assertIn("voice_calibration", clean)
        self.assertIn("forward_ready_packages", clean)
        self.assertIn("style_rewrite_results", clean)


if __name__ == "__main__":
    unittest.main()
