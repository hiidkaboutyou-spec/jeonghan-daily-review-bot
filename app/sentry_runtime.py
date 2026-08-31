from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
from pathlib import Path

from .channel_style_validation import check_project
from .config import ConfigError, Settings
from .observability import capture_technical_exception, init_optional_sentry
from . import final_edit_capture_runtime as _final_edit_capture_runtime
from . import production_outcome_runtime as _outcome_runtime

# Final Edit Capture is deliberately installed only on the normal Daily/private-review
# entrypoint. app.fic_digest imports neither this module nor the capture runtime.
_final_edit_capture_runtime.install()
_outcome_runtime.install()

from .webhook_aware_assistant import WebhookAwarePersonalAssistant


async def async_main() -> int:
    init_optional_sentry()
    # Start structured production outcome tracking
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    trigger_event = os.environ.get("GITHUB_EVENT_NAME", "")
    commit_sha = os.environ.get("GITHUB_SHA", "")
    if not run_id:
        # Local run: derive a stable but non-sensitive run identifier
        commit_sha = _local_commit_sha()
        run_id = f"local-{commit_sha[:12]}" if commit_sha else "local"
    _outcome_runtime.start_run(
        run_id=run_id,
        trigger_event=trigger_event,
        commit_sha=commit_sha,
    )
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        return int(await WebhookAwarePersonalAssistant(settings).run())
    except ConfigError as exc:
        _outcome_runtime.finalize_run()
        capture_technical_exception(exc, component="config")
        return 2
    except Exception as exc:
        _outcome_runtime.finalize_run()
        capture_technical_exception(exc, component="runtime")
        raise


def _local_commit_sha() -> str:
    """Best-effort local commit SHA for non-Actions environments."""
    try:
        result = os.popen("git rev-parse HEAD 2>/dev/null").read().strip()
        if result and len(result) >= 12:
            return result[:40]
    except Exception:
        pass
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check_project()
    return asyncio.run(async_main())
