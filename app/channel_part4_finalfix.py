from __future__ import annotations

"""Compatibility alias for channel source-fact normalization runtime.

The canonical implementation lives in :mod:`app.channel_source_fact_normalization_runtime`.
The legacy path intentionally resolves to the exact same module object so import-time
normalization state cannot diverge during migration.
"""

import sys

from . import channel_source_fact_normalization_runtime as _implementation

sys.modules[__name__] = _implementation
