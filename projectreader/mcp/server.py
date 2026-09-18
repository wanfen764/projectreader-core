"""Model-agnostic stdio MCP server for ProjectReader Core.

The server deliberately owns no repository implementation.  It translates a
small public MCP contract into calls on :class:`projectreader.ProjectReader`.
Repository authority, patch transactions, verification, and audit state stay
inside that object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, BinaryIO, TextIO


MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_MCP_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", MCP_PROTOCOL_VERSION}
)
SERVER_NAME = "projectreader-core"
SERVER_VERSION = "0.1.0a1"


class McpRequestError(ValueError):
    """A JSON-RPC request or tool payload is invalid."""


def _object_schema(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


_EMPTY_SCHEMA = _object_schema({})
_INSPECTION_REF = {
    "type": "string",
    "minLength": 1,
    "description": (
        "Opaque repository-scoped inspection_ref returned by search_repository. "
        "It is not a file path and cannot be invented to read arbitrary files."
    ),
}


CORE_MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "repository_info",
        "description": "Return compact state for the repository opened by this server.",
        "inputSchema": _EMPTY_SCHEMA,
    },
    {
        "name": "index_repository",
        "description": "Build or refresh the bounded ProjectReader repository index.",
        "inputSchema": _EMPTY_SCHEMA,
    },
    {
        "name": "search_repository",
        "description": (
            "Search the registered repository index. Results include opaque "
            "inspection_ref values for exact, controlled inspection."
        ),
        "inputSchema": _object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 10},
            },
            ("query",),
        ),
    },
    {
        "name": "inspect_repository_target",
        "description": (
            "Read bounded source for an exact indexed target without granting "
            "patch authority or arbitrary filesystem access."
        ),
        "inputSchema": _object_schema(
            {
                "inspection_ref": _INSPECTION_REF,
                "segment_index": {"type": "integer", "minimum": 1},
            },
            ("inspection_ref",),
        ),
    },
    {
        "name": "focus_repository_target",
        "description": "Select an indexed target as the current navigation context.",
        "inputSchema": _object_schema(
            {
                "inspection_ref": _INSPECTION_REF,
                "segment_index": {"type": "integer", "minimum": 1},
            },
            ("inspection_ref",),
        ),
    },
    {
        "name": "replace_files",
        "description": (
            "Propose and transactionally apply complete file replacements. Each "
            "content value is the complete new file or null to delete it. Diffs, "
            "edit hunks, and arbitrary shell operations are not accepted."
        ),
        "inputSchema": _object_schema(
            {
                "file_replacements": {
                    "type": "array",
                    "minItems": 1,
                    "items": _object_schema(
                        {
                            "path": {
                                "type": "string",
                                "minLength": 1,
                                "description": "Repository-relative file path.",
                            },
                            "content": {
                                "type": ["string", "null"],
                                "description": "Complete replacement content, or null to delete.",
                            },
                        },
                        ("path", "content"),
                    ),
                },
                "description": {"type": "string"},
            },
            ("file_replacements",),
        ),
    },
    {
        "name": "verify_patch",
        "description": (
            "Run the verifier configured by the human who launched the server. "
            "The tool cannot choose or execute an arbitrary command."
        ),
        "inputSchema": _EMPTY_SCHEMA,
    },
    {
        "name": "accept_patch",
        "description": "Accept the currently applied patch only after successful verification.",
        "inputSchema": _EMPTY_SCHEMA,
    },
    {
        "name": "rollback_patch",
        "description": "Rollback the currently applied patch through its transaction record.",
        "inputSchema": _EMPTY_SCHEMA,
    },
    {
        "name": "get_audit_log",
        "description": "Return recent authoritative Core audit events.",
        "inputSchema": _object_schema(
            {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}
        ),
    },
)


class ProjectReaderMcpAdapter:
    """Translate the public MCP tools into one standalone ProjectReader instance."""

    def __init__(self, reader: Any):
        self.reader = reader

    def call(self, name: str, arguments: Any) -> Any:
        args = _require_object(arguments, "tool arguments")
        if name == "repository_info":
            _require_fields(args, set(), set())
            return self.reader.status()
        if name == "index_repository":
            _require_fields(args, set(), set())
            return self.reader.index()
        if name == "search_repository":
            _require_fields(args, {"query"}, {"query", "limit"})
            query = _require_nonempty_string(args["query"], "query")
            limit = _require_int(args.get("limit", 10), "limit", minimum=1, maximum=10)
            return self.reader.search(query, limit=limit)
        if name == "inspect_repository_target":
            _require_fields(args, {"inspection_ref"}, {"inspection_ref", "segment_index"})
            segment_index = args.get("segment_index")
            if segment_index is not None:
                segment_index = _require_int(
                    segment_index, "segment_index", minimum=1, maximum=1_000_000
                )
            return self.reader.inspect(
                _require_nonempty_string(args["inspection_ref"], "inspection_ref"),
                segment_index=segment_index,
            )
        if name == "focus_repository_target":
            _require_fields(args, {"inspection_ref"}, {"inspection_ref", "segment_index"})
            segment_index = args.get("segment_index")
            if segment_index is not None:
                segment_index = _require_int(
                    segment_index, "segment_index", minimum=1, maximum=1_000_000
                )
            return self.reader.focus(
                _require_nonempty_string(args["inspection_ref"], "inspection_ref"),
                segment_index=segment_index,
            )
        if name == "replace_files":
            return self._replace_files(args)
        if name == "verify_patch":
            _require_fields(args, set(), set())
            return self.reader.verify()
        if name == "accept_patch":
            _require_fields(args, set(), set())
            return self.reader.accept()
        if name == "rollback_patch":
            _require_fields(args, set(), set())
            return self.reader.rollback()
        if name == "get_audit_log":
            _require_fields(args, set(), {"limit"})
            limit = _require_int(args.get("limit", 20), "limit", minimum=1, maximum=100)
            events = self.reader.audit_events
            if callable(events):
                events = events()
            return list(events)[-limit:]
        raise McpRequestError("unknown MCP tool")

    def _replace_files(self, args: Mapping[str, Any]) -> Any:
        _require_fields(args, {"file_replacements"}, {"file_replacements", "description"})
        values = args["file_replacements"]
        if not isinstance(values, list) or not values:
            raise McpRequestError("file_replacements must be a non-empty array")
        replacements: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in values:
            value = _require_object(item, "file replacement")
            _require_fields(value, {"path", "content"}, {"path", "content"})
            path = _require_nonempty_string(value["path"], "path")
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise McpRequestError("path must be repository-relative and may not traverse parents")
            if path in seen:
                raise McpRequestError("duplicate replacement path")
            content = value["content"]
            if content is not None and not isinstance(content, str):
                raise McpRequestError("content must be a string or null")
            seen.add(path)
            replacements.append({"path": path, "content": content})
        description = args.get("description", "")
        if not isinstance(description, str):
            raise McpRequestError("description must be a string")
        return self.reader.patch(replacements, description=description)


class ProjectReaderMcpServer:
    """Strict newline-delimited JSON-RPC 2.0 server over stdio."""

    def __init__(self, adapter: ProjectReaderMcpAdapter):
        self.adapter = adapter
        self._initialize_seen = False
        self._initialized = False

    def handle_message(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, Mapping) or message.get("jsonrpc") != "2.0":
            return _jsonrpc_error(None, -32600, "Invalid Request")
        notification = "id" not in message
        request_id = message.get("id")
        if not notification and not _valid_request_id(request_id):
            return _jsonrpc_error(None, -32600, "Invalid Request")
        method = message.get("method")
        if not isinstance(method, str):
            return _jsonrpc_error(request_id, -32600, "Invalid Request")

        if notification:
            if method == "notifications/initialized" and self._initialize_seen:
                self._initialized = True
            return None

        try:
            if method == "initialize":
                result = self._initialize(message.get("params"))
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                self._require_initialized()
                result = {"tools": list(CORE_MCP_TOOLS)}
            elif method == "tools/call":
                self._require_initialized()
                result = self._call_tool(message.get("params"))
            else:
                return _jsonrpc_error(request_id, -32601, "Method not found")
        except McpRequestError as exc:
            return _jsonrpc_error(request_id, -32602, str(exc))
        except RuntimeError as exc:
            return _jsonrpc_error(request_id, -32000, str(exc))
        except Exception:
            return _jsonrpc_error(request_id, -32603, "Internal error")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def serve(self, input_stream: BinaryIO | TextIO, output_stream: BinaryIO | TextIO) -> None:
        while True:
            raw = input_stream.readline()
            if raw in (b"", ""):
                return
            message: Any = None
            try:
                line = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
                message = _strict_json_loads(line)
                response = self.handle_message(message)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                response = _jsonrpc_error(None, -32700, "Parse error")
            if response is not None:
                try:
                    rendered = json.dumps(response, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                except (TypeError, ValueError):
                    request_id = message.get("id") if isinstance(message, Mapping) else None
                    rendered = json.dumps(_jsonrpc_error(request_id, -32603, "Internal error"), separators=(",", ":"))
                _write_line(output_stream, (rendered + "\n").encode("utf-8"))

    def _initialize(self, params: Any) -> dict[str, Any]:
        if self._initialize_seen:
            raise RuntimeError("MCP server has already received initialize")
        value = _require_object(params, "initialize params")
        requested = value.get("protocolVersion")
        if not isinstance(requested, str):
            raise McpRequestError("initialize requires protocolVersion")
        selected = requested if requested in SUPPORTED_MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
        self._initialize_seen = True
        return {
            "protocolVersion": selected,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError("MCP server has not received notifications/initialized")

    def _call_tool(self, params: Any) -> dict[str, Any]:
        value = _require_object(params, "tools/call params")
        _require_fields(value, {"name"}, {"name", "arguments", "_meta"})
        # MCP request metadata belongs to the protocol envelope, not the tool's
        # semantic arguments. Its contents are opaque to the Core dispatcher.
        if "_meta" in value:
            _require_object(value["_meta"], "tools/call _meta")
        name = _require_nonempty_string(value["name"], "name")
        if name not in {tool["name"] for tool in CORE_MCP_TOOLS}:
            raise McpRequestError("unknown MCP tool")
        try:
            output = self.adapter.call(name, value.get("arguments", {}))
        except McpRequestError:
            raise
        except Exception as exc:
            # Only the Core's closed typed-rejection hierarchy is recoverable.
            # Unknown ValueError/RuntimeError instances must remain fail-closed.
            from projectreader.actions import ProjectReaderRejected

            if isinstance(exc, ProjectReaderRejected):
                error = exc.as_error()
                return _tool_result(
                    {
                        "ok": False,
                        "tool": name,
                        "error": error,
                    },
                    is_error=True,
                )
            return _tool_result(
                {
                    "ok": False,
                    "tool": name,
                    "error": {
                        "error_type": "core_internal_error",
                        "recoverable": False,
                        "side_effects": "unknown",
                    },
                },
                is_error=True,
            )
        return _tool_result({"ok": True, "tool": name, "result": _json_value(output)}, is_error=False)


def create_server(repository_root: str | Path, *, verifier: Sequence[str] | None = None) -> ProjectReaderMcpServer:
    """Create a standalone server without importing any model/provider integration."""
    from projectreader import ProjectReader

    reader = ProjectReader.open(repository_root, verifier=verifier)
    reader.index()
    return ProjectReaderMcpServer(ProjectReaderMcpAdapter(reader))


def serve_repository(
    repository_root: str | Path,
    *,
    verifier: Sequence[str] | None = None,
    input_stream: BinaryIO | TextIO,
    output_stream: BinaryIO | TextIO,
) -> None:
    create_server(repository_root, verifier=verifier).serve(input_stream, output_stream)


def _tool_result(payload: Mapping[str, Any], *, is_error: bool) -> dict[str, Any]:
    normalized = _json_value(payload)
    text = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": normalized,
        "isError": is_error,
    }


def _json_value(value: Any) -> Any:
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _json_value(value.as_dict())
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported result value: {type(value).__name__}")


def _require_object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise McpRequestError(f"{name} must be an object")
    return value


def _require_fields(value: Mapping[str, Any], required: set[str], allowed: set[str]) -> None:
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise McpRequestError("missing required fields: " + ", ".join(sorted(missing)))
    if unknown:
        raise McpRequestError("unsupported fields: " + ", ".join(sorted(str(item) for item in unknown)))


def _require_nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise McpRequestError(f"{name} must be a non-empty string")
    return value


def _require_int(value: Any, name: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise McpRequestError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def _strict_json_loads(value: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = item
        return result

    def invalid_constant(_: str) -> None:
        raise ValueError("non-finite JSON number")

    return json.loads(value, object_pairs_hook=pairs, parse_constant=invalid_constant)


def _valid_request_id(value: Any) -> bool:
    return value is None or isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write_line(stream: BinaryIO | TextIO, payload: bytes) -> None:
    try:
        stream.write(payload)  # type: ignore[arg-type]
    except TypeError:
        stream.write(payload.decode("utf-8"))  # type: ignore[arg-type]
    stream.flush()


__all__ = [
    "CORE_MCP_TOOLS",
    "MCP_PROTOCOL_VERSION",
    "ProjectReaderMcpAdapter",
    "ProjectReaderMcpServer",
    "create_server",
    "serve_repository",
]
