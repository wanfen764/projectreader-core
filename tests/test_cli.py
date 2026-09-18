from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from projectreader.cli import build_parser
from projectreader.mcp.__main__ import parse_verifier


class CliTests(unittest.TestCase):
    def test_parser_has_public_commands(self):
        parser = build_parser()
        help_text = parser.format_help()
        for command in ("index", "search", "inspect", "mcp"):
            self.assertIn(command, help_text)

    def test_verifier_parser_requires_json_argv(self):
        self.assertEqual(parse_verifier('["python","verify.py"]'), ("python", "verify.py"))
        with self.assertRaises(ValueError):
            parse_verifier('"python verify.py"')
        with self.assertRaises(ValueError):
            parse_verifier("[]")

    def test_module_help_cold_start_from_empty_cwd(self):
        extraction_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(extraction_root)
            completed = subprocess.run(
                [sys.executable, "-B", "-m", "projectreader.cli", "--help"],
                cwd=temporary,
                env=environment,
                capture_output=True,
                check=False,
                timeout=15,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        self.assertIn(b"projectreader", completed.stdout)

    def test_index_search_and_inspect_emit_json(self):
        extraction_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repo"
            repository.mkdir()
            (repository / "sample.py").write_text(
                "def useful_value():\n    return 42\n",
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(extraction_root)
            for arguments in (
                ["index", str(repository)],
                ["search", str(repository), "useful_value"],
                ["inspect", str(repository), "useful_value"],
            ):
                completed = subprocess.run(
                    [sys.executable, "-B", "-m", "projectreader.cli", *arguments],
                    cwd=temporary,
                    env=environment,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
                json.loads(completed.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
