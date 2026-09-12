from __future__ import annotations

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.local_runtime import install_launch_agent


class LaunchAgentInstallerTests(unittest.TestCase):
    def test_installer_preserves_venv_python_entrypoint(self):
        root = Path("/tmp/project").resolve()
        python = root / ".venv" / "bin" / "python"
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            with (
                patch.object(install_launch_agent, "ROOT", root),
                patch.object(install_launch_agent.sys, "prefix", str(root / ".venv")),
                patch.object(install_launch_agent.sys, "executable", str(python)),
                patch.object(Path, "home", return_value=home),
            ):
                self.assertEqual(install_launch_agent.main(), 0)

            plist_path = home / "Library" / "LaunchAgents" / "com.hiidkaboutyou.jeonghan-daily-review-bot.plist"
            with plist_path.open("rb") as stream:
                payload = plistlib.load(stream)
            self.assertEqual(payload["ProgramArguments"][0], str(python))


if __name__ == "__main__":
    unittest.main()
