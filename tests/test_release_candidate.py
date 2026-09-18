from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from projectreader import __version__
from projectreader.mcp.server import CORE_MCP_TOOLS


RELEASE_ROOT = Path(__file__).resolve().parents[1]


class ReleaseCandidateContractTests(unittest.TestCase):
    maxDiff = None

    def run_from_empty_cwd(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(RELEASE_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            return subprocess.run(
                [sys.executable, "-B", *arguments],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )

    def test_readme_has_required_public_sections(self):
        readme = (RELEASE_ROOT / "README.md").read_text(encoding="utf-8")
        expected = (
            "What it is",
            "Why it exists",
            "Features",
            "Installation",
            "Python Quickstart",
            "CLI Quickstart",
            "MCP Quickstart",
            "Patch Lifecycle",
            "Safety Model",
            "Auditability",
            "Model Agnosticism",
            "Architecture",
            "Current Alpha Status",
            "Known Limitations",
            "Development / Tests",
            "License",
            "Roadmap",
        )
        headings = set(re.findall(r"^## (.+)$", readme, flags=re.MULTILINE))
        self.assertEqual([item for item in expected if item not in headings], [])

    def test_documented_python_quickstart_runs(self):
        readme = (RELEASE_ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("<!-- BEGIN PYTHON QUICKSTART -->")
        end = readme.index("<!-- END PYTHON QUICKSTART -->", start)
        block = readme[start:end]
        match = re.search(r"```python\n(?P<code>.*?)\n```", block, flags=re.DOTALL)
        self.assertIsNotNone(match)
        completed = subprocess.run(
            [sys.executable, "-B", "-c", match.group("code")],
            cwd=RELEASE_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(completed.stdout.strip())

    def test_documented_cli_commands_run(self):
        repository = RELEASE_ROOT / "examples" / "demo_repository"
        commands = (
            ["-m", "projectreader", "--help"],
            ["-m", "projectreader", "index", str(repository)],
            ["-m", "projectreader", "search", str(repository), "greeting"],
            ["-m", "projectreader", "inspect", str(repository), "greeting"],
            ["-m", "projectreader.mcp", "--help"],
        )
        for command in commands:
            with self.subTest(command=command):
                completed = self.run_from_empty_cwd(command)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(completed.stdout.strip())

    def test_readme_mcp_tool_list_matches_runtime(self):
        readme = (RELEASE_ROOT / "README.md").read_text(encoding="utf-8")
        section = readme.split("The MCP client cannot replace that command. Current tools are:", 1)[1]
        section = section.split("The consumer can be", 1)[0]
        documented = re.findall(r"^- `([^`]+)`$", section, flags=re.MULTILINE)
        self.assertEqual(documented, [item["name"] for item in CORE_MCP_TOOLS])

    def test_package_metadata_is_consistent_and_public_urls_are_pending(self):
        content = (RELEASE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(content, r'(?m)^name = "projectreader-core"$')
        self.assertRegex(content, rf'(?m)^version = "{re.escape(__version__)}"$')
        self.assertRegex(content, r'(?m)^authors = \[\{ name = "Asagiri Nightrin" \}\]$')
        self.assertRegex(content, r'(?m)^license = "Apache-2\.0"$')
        self.assertRegex(content, r'(?m)^license-files = \["LICENSE"\]$')
        self.assertRegex(content, r'(?m)^requires-python = ">=3\.10"$')
        self.assertRegex(content, r'(?m)^dependencies = \[\]$')
        self.assertIn(
            'description = "A model-agnostic, controlled, and auditable '
            'repository-engineering layer for AI coding agents"',
            content,
        )
        self.assertNotIn("[project.urls]", content)
        pending = (RELEASE_ROOT / "PUBLIC_METADATA_REQUIRED.md").read_text(encoding="utf-8")
        self.assertIn("public source repository URL", pending)
        self.assertNotIn("USER_METADATA_REQUIRED", pending)

    def test_build_backend_floor_supports_spdx_license_metadata(self):
        # Build-time support must match project.license/project.license-files.
        # The runtime package still has no mandatory third-party dependencies.
        content = (RELEASE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        build_section = content.split("[build-system]", 1)[1].split("[project]", 1)[0]
        match = re.search(r'"setuptools>=(\d+)(?:\.(\d+))?(?:\.(\d+))?"', build_section)
        self.assertIsNotNone(match, "Declare an explicit PEP 639-capable backend minimum")
        self.assertGreaterEqual(tuple(int(part or 0) for part in match.groups()), (77, 0, 3))

    def test_apache_license_and_security_policy_are_final(self):
        license_text = (RELEASE_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0, January 2004", license_text)
        self.assertIn("END OF TERMS AND CONDITIONS", license_text)
        self.assertIn("APPENDIX: How to apply the Apache License to your work", license_text)
        self.assertFalse((RELEASE_ROOT / "LICENSE_SELECTION_REQUIRED.md").exists())
        self.assertFalse((RELEASE_ROOT / "docs" / "license-options.md").exists())
        security = (RELEASE_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        normalized_security = " ".join(security.split())
        self.assertIn("Alpha", security)
        self.assertIn("GitHub Private Vulnerability Reporting", normalized_security)
        self.assertIn("Never paste credentials", normalized_security)
        readme = (RELEASE_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Author: Asagiri Nightrin", readme)
        self.assertIn("License: Apache-2.0", readme)

    def test_gitignore_has_release_hygiene_patterns(self):
        entries = set((RELEASE_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        required = {".venv/", "__pycache__/", "*.py[cod]", ".pytest_cache/", "build/", "dist/", "*.egg-info/", "*.log"}
        self.assertEqual(required - entries, set())

    def test_ci_matrix_and_commands_are_declared(self):
        workflow = (RELEASE_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertNotIn("\t", workflow)
        for value in ("ubuntu-latest", "windows-latest", "macos-latest", '"3.10"', '"3.13"'):
            self.assertIn(value, workflow)
        for command in (
            "python -B -m unittest discover -s tests -v",
            "projectreader --help",
            "projectreader index examples/demo_repository",
            "projectreader search examples/demo_repository greeting",
            "projectreader inspect examples/demo_repository greeting",
        ):
            self.assertIn(command, workflow)

    def test_public_tree_has_no_internal_history_terms(self):
        terms = (
            "Investigation" + "Session",
            "experimental" + "_delta",
            "fast" + "_v1",
            "trial " + "descriptor",
            "phase" + "2 experiment",
        )
        hits: list[tuple[str, str]] = []
        roots = (RELEASE_ROOT / "projectreader", RELEASE_ROOT / "docs", RELEASE_ROOT / "examples")
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".md", ".txt"}:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
                for term in terms:
                    if term.lower() in content.lower():
                        hits.append((path.relative_to(RELEASE_ROOT).as_posix(), term))
        self.assertEqual(hits, [])

    def test_full_quickstart_runs_from_source_checkout(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(RELEASE_ROOT)
        completed = subprocess.run(
            [sys.executable, "-B", str(RELEASE_ROOT / "examples" / "quickstart.py")],
            cwd=RELEASE_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("status='accepted'", completed.stdout)
        self.assertIn("'patch_verified': 1", completed.stdout)


if __name__ == "__main__":
    unittest.main()
