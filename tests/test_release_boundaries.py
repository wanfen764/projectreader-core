from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


EXTRACTION_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = EXTRACTION_ROOT / "projectreader"


class ReleaseBoundaryTests(unittest.TestCase):
    def test_runtime_static_imports_exclude_non_core_modules(self):
        forbidden_roots = {
            "bench" + "marking",
            "bench" + "marks",
            "core",
            "modeling",
            "open" + "ai",
            "pydantic",
        }
        violations = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".", 1)[0] in forbidden_roots:
                        violations.append((path.relative_to(EXTRACTION_ROOT).as_posix(), name))
        self.assertEqual(violations, [])

    def test_dynamic_import_closure_has_no_external_project_packages(self):
        prefixes = (
            "core",
            "modeling",
            "bench" + "marking",
            "bench" + "marks",
        )
        code = (
            "import json,sys,projectreader;prefixes=" + repr(prefixes) + ";"
            "print(json.dumps(sorted(name for name in sys.modules "
            "if name.startswith(prefixes))))"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(EXTRACTION_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                [sys.executable, "-B", "-c", code],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "[]")

    def test_runtime_has_no_private_session_type_or_local_absolute_path(self):
        forbidden_text = (
            "Investigation" + "Session",
            "private-" + "control" + "ler-checkout",
        )
        private_path_patterns = {
            "windows_user_path": re.compile(
                r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+",
                re.IGNORECASE,
            ),
            "posix_user_path": re.compile(
                r"/(?:Users|home)/[^/\s]+",
                re.IGNORECASE,
            ),
        }
        hits = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            for value in forbidden_text:
                if value in content:
                    hits.append((path.relative_to(EXTRACTION_ROOT).as_posix(), value))
            for label, pattern in private_path_patterns.items():
                if pattern.search(content):
                    hits.append((path.relative_to(EXTRACTION_ROOT).as_posix(), label))
        self.assertEqual(hits, [])

    def test_extraction_tree_contains_no_symlink(self):
        links = [
            path.relative_to(EXTRACTION_ROOT).as_posix()
            for path in EXTRACTION_ROOT.rglob("*")
            if path.is_symlink()
        ]
        self.assertEqual(links, [])

    def test_copy_to_second_location_import_and_cli_cold_start(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            relocated = temporary / "relocated"
            relocated.mkdir()
            shutil.copytree(PACKAGE_ROOT, relocated / "projectreader")
            empty_cwd = temporary / "empty"
            empty_cwd.mkdir()
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(relocated)
            for arguments in (
                ["-c", "import projectreader; print(projectreader.__version__)"],
                ["-m", "projectreader", "--help"],
                ["-m", "projectreader.mcp", "--help"],
            ):
                completed = subprocess.run(
                    [sys.executable, "-B", *arguments],
                    cwd=empty_cwd,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
