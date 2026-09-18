from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


EXTRACTION_ROOT = Path(__file__).resolve().parents[1]


class McpEndToEndTests(unittest.TestCase):
    def test_one_stdio_session_searches_patches_verifies_and_accepts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repository"
            root.mkdir()
            (root / "app.py").write_bytes(
                b"def calculate_value():\n    return 1\n"
            )
            (root / "verify.py").write_bytes(
                b"from app import calculate_value\nassert calculate_value() == 2\n"
            )
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(EXTRACTION_ROOT)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "projectreader.mcp",
                    "--repo",
                    str(root),
                    "--verifier-command-json",
                    json.dumps([sys.executable, "verify.py"]),
                ],
                cwd=directory,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            def exchange(message):
                encoded = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
                process.stdin.write(encoded)
                process.stdin.flush()
                return json.loads(process.stdout.readline().decode("utf-8"))

            try:
                initialized = exchange(
                    {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                    }
                )
                self.assertEqual(initialized["result"]["serverInfo"]["name"], "projectreader-core")
                process.stdin.write(
                    b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                )
                process.stdin.flush()
                tools = exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                names = [item["name"] for item in tools["result"]["tools"]]
                self.assertIn("replace_files", names)

                search = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "search_repository",
                        "arguments": {"query": "calculate_value"},
                    },
                }
                )["result"]["structuredContent"]
                reference = next(
                item["inspection_ref"]
                for item in search["result"]
                if item["target_type"] == "symbol"
                )
                inspected = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "inspect_repository_target",
                        "arguments": {"inspection_ref": reference},
                    },
                }
                )["result"]
                self.assertIn("return 1", inspected["structuredContent"]["result"]["content"])

                applied = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "replace_files",
                        "arguments": {
                            "file_replacements": [
                                {
                                    "path": "app.py",
                                    "content": "def calculate_value():\n    return 2\n",
                                }
                            ],
                            "description": "MCP lifecycle test",
                        },
                    },
                }
                )["result"]
                self.assertFalse(applied["isError"])
                self.assertEqual(
                    applied["structuredContent"]["result"]["status"],
                    "applied",
                )
                verified = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {"name": "verify_patch", "arguments": {}},
                }
                )["result"]
                self.assertTrue(verified["structuredContent"]["result"]["passed"])
                accepted = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "accept_patch", "arguments": {}},
                }
                )["result"]
                self.assertEqual(
                    accepted["structuredContent"]["result"]["status"],
                    "accepted",
                )
                audit = exchange(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tools/call",
                    "params": {"name": "get_audit_log", "arguments": {}},
                }
                )["result"]["structuredContent"]["result"]
                self.assertEqual(audit[-1]["event_type"], "patch_accepted")
                process.stdin.close()
                returncode = process.wait(timeout=20)
                stderr = process.stderr.read()
                self.assertEqual(returncode, 0, stderr.decode("utf-8", errors="replace"))
                self.assertEqual(stderr, b"")
                self.assertIn("return 2", (root / "app.py").read_text(encoding="utf-8"))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None and not stream.closed:
                        stream.close()


if __name__ == "__main__":
    unittest.main()
