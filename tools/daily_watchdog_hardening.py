from __future__ import annotations

"""Compatibility entrypoint for the historical Daily watchdog hardening path.

The canonical credential-safe transport now lives in
``tools.daily_watchdog_transport``. Imports of this historical module are
aliased to that canonical module so monkey-patching/tests observe one module
state, while direct CLI execution keeps the old command working.
"""

import sys

try:
    from tools import daily_watchdog_transport as _transport
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog_transport as _transport


if __name__ == "__main__":
    raise SystemExit(_transport._watchdog.main())

# A module alias avoids maintaining duplicate private symbols or mutable module
# state during the compatibility window. Existing imports transparently receive
# the canonical transport implementation.
sys.modules[__name__] = _transport
