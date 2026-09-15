from __future__ import annotations

"""Compatibility alias for the renamed degraded-X recovery runtime.

The canonical implementation lives in :mod:`app.x_degraded_recovery_runtime`.
This legacy import path intentionally resolves to the exact same module object so
mutable install flags and monkey-patched provider state cannot diverge while
callers migrate. Remove this shim only after the migration registry gates pass.
"""

import sys

from . import x_degraded_recovery_runtime as _implementation

# A normal ``from ... import *`` or attribute re-export would copy bindings into a
# second module namespace. This module owns mutable idempotency flags, so both old
# and new import paths must resolve to one shared module object during migration.
sys.modules[__name__] = _implementation
