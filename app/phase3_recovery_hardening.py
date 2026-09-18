from __future__ import annotations

"""Compatibility alias for the X recovery integrity runtime.

The canonical implementation lives in :mod:`app.x_recovery_integrity_runtime`.
The historical path resolves to the exact same module object so import-time
patching of :mod:`app.x_resumable_recovery_runtime` can never execute against
a duplicate integrity module identity.
"""

import sys

from . import x_recovery_integrity_runtime as _implementation

sys.modules[__name__] = _implementation
