from __future__ import annotations

"""Compatibility alias for the resumable X recovery runtime.

The canonical implementation lives in :mod:`app.x_resumable_recovery_runtime`.
The legacy path intentionally resolves to the exact same Python module object so
checkpoint helpers, collector patches, provider-proof patches, and integrity
hardening can never split across duplicate recovery state.
"""

import sys

from . import x_resumable_recovery_runtime as _implementation

sys.modules[__name__] = _implementation
