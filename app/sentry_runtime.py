from __future__ import annotations

import argparse
import asyncio

from .channel_style_validation import check_project
from .config import ConfigError, Settings
from .observability import capture_technical_exception, init_optional_sentry
from . import final_edit_capture_runtime as _final_edit_capture_runtime

# Final Edit Capture is deliberately installed only on the normal Daily/private-review
# entrypoint. app.fic_digest imports neither this module nor the capture runtime.
_final_edit_capture_runtime.install()

from .webhook_aware_assistant import WebhookAwarePersonalAssistant


async def async_main() -> int:
    init_optional_sentry()
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        return int(await WebhookAwarePersonalAssistant(settings).run())
    except ConfigError as exc:
        capture_technical_exception(exc, component="config")
        return 2
    except Exception as exc:
        capture_technical_exception(exc, component="runtime")
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check_project()
    return asyncio.run(async_main())
