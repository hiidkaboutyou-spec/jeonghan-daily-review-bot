from __future__ import annotations

"""Compatibility entrypoint for the Daily watchdog artifact transport.

The credential-safe artifact transport now lives in
``tools.daily_watchdog_transport``. This historical path remains executable and
keeps its import-time installation behavior until all callers have migrated.
"""

try:
    from tools import daily_watchdog as _watchdog
    from tools import daily_watchdog_transport as _transport
except ModuleNotFoundError:  # direct `python tools/...py` execution
    import daily_watchdog as _watchdog
    import daily_watchdog_transport as _transport

# Re-export the historical test/operator surface while the compatibility path
# remains supported. These aliases intentionally reference the canonical
# transport implementation rather than duplicating code.
_NoRedirect = _transport._NoRedirect
_download_zip_without_cross_origin_auth = (
    _transport._download_zip_without_cross_origin_auth
)
_fetch_latest_production_outcome = _transport._fetch_latest_production_outcome
request = _transport.request
time = _transport.time
error = _transport.error

# Preserve the historical module's import-time installation contract.
_transport.install()


if __name__ == "__main__":
    raise SystemExit(_watchdog.main())
