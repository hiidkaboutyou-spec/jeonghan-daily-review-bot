from __future__ import annotations

"""Compatibility alias for the canonical X resumable-recovery runtime.

The implementation lives in :mod:`app.x_resumable_recovery_runtime`.
The historical path intentionally resolves to the exact same module object so
checkpoint globals and import-time hardening/provider-proof patches cannot split
across duplicate module state.
"""

import sys

from . import x_resumable_recovery_runtime as _implementation

sys.modules[__name__] = _implementation
