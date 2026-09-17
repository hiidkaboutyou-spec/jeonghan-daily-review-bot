from __future__ import annotations

"""Compatibility alias for the channel human-quality-gate runtime.

The canonical implementation lives in :mod:`app.channel_human_quality_gate_runtime`.
The legacy path intentionally resolves to the exact same Python module object because
quality-repair layers mutate human-gate fingerprint and verifier bindings at runtime.
"""

import sys

from . import channel_human_quality_gate_runtime as _implementation

sys.modules[__name__] = _implementation
