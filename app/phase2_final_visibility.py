from __future__ import annotations

"""Compatibility alias for lifecycle outcome visibility runtime.

The canonical implementation lives in :mod:`app.lifecycle_outcome_visibility_runtime`.
The legacy path intentionally resolves to the exact same module object so import-time
installation and idempotency markers cannot diverge during migration.
"""

import sys

from . import lifecycle_outcome_visibility_runtime as _implementation

sys.modules[__name__] = _implementation
