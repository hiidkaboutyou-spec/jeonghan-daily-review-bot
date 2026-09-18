from __future__ import annotations

"""Compatibility alias for the channel quality-repair runtime.

The canonical implementation lives in :mod:`app.channel_quality_repair_runtime`.
The legacy path intentionally resolves to the exact same Python module object so
patching module globals or inspecting the repair layer cannot create split state.
"""

import sys

from . import channel_quality_repair_runtime as _implementation

sys.modules[__name__] = _implementation
