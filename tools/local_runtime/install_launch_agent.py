from __future__ import annotations

import os
import plistlib
import stat
import sys
from pathlib import Path

from app.config import ROOT

LABEL = "com.hiidkaboutyou.jeonghan-daily-review-bot"


def main() -> int:
    python = Path(sys.executable).resolve()
    if ROOT.joinpath(".venv").resolve() not in python.parents:
        raise SystemExit("Run this installer with the repository .venv Python.")
    support = Path.home() / "Library" / "Application Support" / "jeonghan-daily-review-bot"
    logs = Path.home() / "Library" / "Logs" / "jeonghan-daily-review-bot"
    agents = Path.home() / "Library" / "LaunchAgents"
    for directory in (support, logs, agents):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    plist_path = agents / f"{LABEL}.plist"
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(python), "-m", "tools.local_runtime.run_once"],
        "WorkingDirectory": str(ROOT.resolve()),
        "StartInterval": 300,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "runtime.log"),
        "StandardErrorPath": str(logs / "runtime-error.log"),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    temporary = plist_path.with_suffix(".plist.tmp")
    with temporary.open("wb") as stream:
        plistlib.dump(payload, stream, sort_keys=True)
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, plist_path)
    print(f"Wrote {plist_path}")
    print(f"Install with: launchctl bootstrap gui/{os.getuid()} {plist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
