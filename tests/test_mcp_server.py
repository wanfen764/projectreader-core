from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from projectreader.mcp.server import (
    CORE_MCP_TOOLS,
    ProjectReaderMcpAdapter,
    ProjectReaderMcpServer,
)
from projectreader.actions import PatchLifecycleRejected


@dataclass
class FakeResult:
    value: str


class FakeReader:
    def __init__(self):
        self.calls = []
        self.audit_events = [{"sequence": 1, "event_type": "repository_opened"}]

    def status(self):
        self.calls.append(("status",))
        return {"state": "open"}

    def index(self):
        self.calls.append(("index",))
        return {"sources": 2}

    def search(self, query, *, limit):
        self.calls.append(("search", query, limit))
        return [FakeResult("你好")]

    def inspect(self, inspection_ref):
        self.calls.append(("inspect", inspection_ref))
        return {"source": "def 你好():\n    pass\n"}

    def focus(self, inspection_ref):
        self.calls.append(("focus", inspection_ref))
        return {"focused": inspection_ref}

    def patch(self, replacements, *, description):
        self.calls.append(("patch", replacements, description))
        return {"patch_id": "patch-1"}

    def verify(self):
        self.calls.append(("verify",))
        return {"passed": True}

    def accept(self):
        self.calls.append(("accept",))
        return {"outcome": "accepted"}

    def rollback(self):
        self.calls.append(("rollback",))
        return {"outcome": "rolled_back"}


class ProjectReaderMcpServerTests(unittest.TestCase):
    def setUp(self):
        self.reader = FakeReader()
        self.server = ProjectReaderMcpServer(ProjectReaderMcpAdapter(self.reader))

    def initialize(self):
        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(response["result"]["serverInfo"]["name"], "projectreader-core")
        self.server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_tools_list_is_core_only(self):
        self.initialize()
        response = self.server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(names, [tool["name"] for tool in CORE_MCP_TOOLS])

    def test_search_call_preserves_utf8_and_dual_result_representation(self):
        self.initialize()
        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "search_repository", "arguments": {"query": "你好", "limit": 3}},
            }
        )
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        self.assertEqual(result["structuredContent"]["result"][0]["value"], "你好")
        self.assertEqual(self.reader.calls[-1], ("search", "你好", 3))

    def test_replace_files_validates_and_passes_complete_replacements(self):
        self.initialize()
        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "replace_files",
                    "arguments": {
                        "file_replacements": [
                            {"path": "pkg/a.py", "content": "VALUE = 1\n"},
                            {"path": "pkg/old.py", "content": None},
                        ],
                        "description": "update values",
                    },
                },
            }
        )
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(self.reader.calls[-1][0], "patch")
        self.assertEqual(self.reader.calls[-1][1][1]["content"], None)

    def test_replace_files_rejects_parent_traversal_and_duplicate_path(self):
        self.initialize()
        traversal = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "replace_files",
                    "arguments": {"file_replacements": [{"path": "../outside.py", "content": "x"}]},
                },
            }
        )
        self.assertEqual(traversal["error"]["code"], -32602)
        duplicate = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "replace_files",
                    "arguments": {
                        "file_replacements": [
                            {"path": "a.py", "content": "x"},
                            {"path": "a.py", "content": "y"},
                        ]
                    },
                },
            }
        )
        self.assertEqual(duplicate["error"]["code"], -32602)
        self.assertFalse(any(call[0] == "patch" for call in self.reader.calls))

    def test_allowlisted_core_rejection_is_structured_and_recoverable(self):
        self.initialize()

        def rejected_verify():
            raise PatchLifecycleRejected("patch_unknown", "verify_patch")

        self.reader.verify = rejected_verify
        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "verify_patch", "arguments": {}},
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"]["error_type"], "patch_lifecycle_precondition")
        self.assertEqual(result["structuredContent"]["error"]["reason_code"], "patch_unknown")
        self.assertTrue(result["structuredContent"]["error"]["recoverable"])
        self.assertFalse(result["structuredContent"]["error"]["side_effects"])

    def test_unknown_runtime_failure_stays_fail_closed(self):
        self.initialize()

        def broken_verify():
            raise ValueError("must not cross the MCP boundary")

        self.reader.verify = broken_verify
        response = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "verify_patch", "arguments": {}},
            }
        )
        error = response["result"]["structuredContent"]["error"]
        self.assertEqual(error["error_type"], "core_internal_error")
        self.assertFalse(error["recoverable"])
        self.assertNotIn("must not cross", json.dumps(error))

    def test_stdio_lifecycle_outputs_only_jsonrpc(self):
        incoming = b"".join(
            [
                b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n',
                b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
                b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            ]
        )
        output = BytesIO()
        self.server.serve(BytesIO(incoming), output)
        messages = [json.loads(line) for line in output.getvalue().decode("utf-8").splitlines()]
        self.assertEqual([item["id"] for item in messages], [1, 2])
        self.assertEqual(messages[1]["result"]["tools"], list(CORE_MCP_TOOLS))

    def test_tools_are_unavailable_before_initialized_notification(self):
        self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        response = self.server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(response["error"]["code"], -32000)


class ProjectReaderMcpColdStartTests(unittest.TestCase):
    def test_fresh_process_empty_cwd_initialize_and_tools_list(self):
        extraction_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            repository = temporary_root / "repo"
            repository.mkdir()
            (repository / "sample.py").write_text("def sample():\n    return 1\n", encoding="utf-8")
            empty_cwd = temporary_root / "empty"
            empty_cwd.mkdir()
            environment = dict(__import__("os").environ)
            environment["PYTHONPATH"] = str(extraction_root)
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "projectreader.mcp", "--repo", str(repository)],
                cwd=empty_cwd,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            requests = b"".join(
                [
                    b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}\n',
                    b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n',
                    b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
                ]
            )
            stdout, stderr = process.communicate(requests, timeout=15)
            self.assertEqual(process.returncode, 0, stderr.decode("utf-8", errors="replace"))
            self.assertEqual(stderr, b"")
            messages = [json.loads(line) for line in stdout.decode("utf-8").splitlines()]
            self.assertEqual(messages[0]["result"]["serverInfo"]["name"], "projectreader-core")
            self.assertIn("search_repository", [t["name"] for t in messages[1]["result"]["tools"]])


if __name__ == "__main__":
    unittest.main()
