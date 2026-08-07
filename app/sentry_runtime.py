from __future__ import annotations

import argparse
import asyncio

from .config import ConfigError, Settings
from .main import check_project
from .observability import capture_technical_exception, init_optional_sentry
from .reminder_runtime import ReminderReviewApplication


async def async_main() -> int:
    init_optional_sentry()
    try:
        settings = Settings.load(require_secrets=True)
        errors = settings.validate_files()
        if errors:
            raise ConfigError("; ".join(errors))
        await ReminderReviewApplication(settings).run()
        return 0
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
