from __future__ import annotations

"""Compatibility entrypoint for the historical Daily watchdog hardening path.

The canonical credential-safe transport now lives in
``tools.daily_watchdog_transport``. Keep this module as a thin compatibility
shim so old workflow references or operator commands remain safe during the
Stage C migration.
"""

try:
    from tools import daily_watchdog_transport as _transport
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog_transport as _transport

# Preserve the historical module surface for tests and any internal callers.
_watchdog = _transport._watchdog
_NoRedirect = _transport._NoRedirect
_download_zip_without_cross_origin_auth = (
    _transport._download_zip_without_cross_origin_auth
)
_fetch_latest_production_outcome = _transport._fetch_latest_production_outcome
install_transport = _transport.install_transport
request = _transport.request
error = _transport.error
time = _transport.time


if __name__ == "__main__":
    raise SystemExit(_watchdog.main())
