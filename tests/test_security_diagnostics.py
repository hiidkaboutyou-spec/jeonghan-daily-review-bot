from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKOUT_SHA = "d23441a48e516b6c34aea4fa41551a30e30af803"
SETUP_PYTHON_SHA = "ece7cb06caefa5fff74198d8649806c4678c61a1"
UPLOAD_ARTIFACT_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
CODEQL_SHA = "b96794f015dfd88f77b49b1c93e0fa7110f94c63"
PIP_AUDIT_SHA = "8894eb8cee033531a1fbd9f2fb160892531c14e3"
BANDIT_SHA = "92ae8b82fb422a639f0ed8d99e96cea769594e08"


class SecurityDiagnosticsConfigurationTests(unittest.TestCase):
    def test_security_workflow_is_pinned_and_secret_free(self) -> None:
        text = (ROOT / ".github" / "workflows" / "security-diagnostics.yml").read_text(encoding="utf-8")
        for sha in (CHECKOUT_SHA, SETUP_PYTHON_SHA, UPLOAD_ARTIFACT_SHA):
            self.assertIn(sha, text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("dependency-review-action", text)
        self.assertIn("--severity-level high", text)
        self.assertIn("--confidence-level high", text)
        self.assertIn("--vulnerability-service osv", text)

    def test_codeql_uses_interpreted_language_mode_and_narrow_permission(self) -> None:
        text = (ROOT / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count(CODEQL_SHA), 2)
        self.assertIn("languages: python", text)
        self.assertIn("build-mode: none", text)
        self.assertIn("security-events: write", text)
        self.assertNotIn("secrets.", text)

    def test_security_tools_are_git_pinned_and_not_production_dependencies(self) -> None:
        security = (ROOT / "requirements-security.txt").read_text(encoding="utf-8")
        production = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn(PIP_AUDIT_SHA, security)
        self.assertIn(BANDIT_SHA, security)
        self.assertNotIn("pip-audit", production)
        self.assertNotIn("bandit", production)
        for line in security.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            self.assertRegex(line, r"@[0-9a-f]{40}$")

    def test_manifest_records_active_scanners_as_non_runtime(self) -> None:
        manifest = json.loads((ROOT / "config" / "agent_capabilities.json").read_text(encoding="utf-8"))
        sources = {item["id"]: item for item in manifest["sources"]}
        expected = {
            "github-codeql": CODEQL_SHA,
            "pypa-pip-audit": PIP_AUDIT_SHA,
            "pycqa-bandit": BANDIT_SHA,
        }
        for source_id, commit in expected.items():
            self.assertIn(source_id, sources)
            self.assertEqual(sources[source_id]["commit"], commit)
            self.assertIs(sources[source_id]["runtime"], False)
        dependency_review = sources["github-dependency-review"]
        self.assertEqual(dependency_review["decision"], "reference-only")
        self.assertIs(dependency_review["runtime"], False)
        skill = next(item for item in manifest["active_skills"] if item["path"].endswith("hani-incident-triage/SKILL.md"))
        self.assertEqual(set(skill["source_refs"]), set(expected))

    def test_workflow_uses_only_full_sha_action_pins(self) -> None:
        for relative in (
            Path(".github/workflows/security-diagnostics.yml"),
            Path(".github/workflows/codeql.yml"),
        ):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for action, ref in re.findall(r"uses:\s*([^@\s]+)@([^\s#]+)", text):
                self.assertRegex(ref, r"^[0-9a-f]{40}$", f"{relative}: {action}@{ref} is not a full SHA pin")


if __name__ == "__main__":
    unittest.main()
