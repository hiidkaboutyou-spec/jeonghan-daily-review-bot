from __future__ import annotations

"""Compatibility alias for the semantic lifecycle-correlation runtime.

The canonical implementation lives in :mod:`app.lifecycle_correlation_runtime`.
The legacy path intentionally resolves to the exact same module object so import-
time installation and idempotency markers cannot diverge during migration.
"""

import sys

from . import lifecycle_correlation_runtime as _implementation

sys.modules[__name__] = _implementation
