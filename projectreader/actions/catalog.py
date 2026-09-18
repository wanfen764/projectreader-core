"""Closed Core action and verifier command catalogs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .models import FileReplacement, StructuredAction
from .rejections import ActionPayloadRejected, CommandSelectionRejected


@dataclass(frozen=True, slots=True)
class RegisteredCommand:
    command_id: str
    argv: tuple[str, ...]
    description: str = ""
    purpose: str = "verification"


class CommandCatalog:
    """Host-controlled exact argv commands.  Shell expansion is never used."""

    def __init__(self) -> None:
        self._commands: dict[str, RegisteredCommand] = {}

    def register(
        self,
        command_id: str,
        argv,
        *,
        description: str = "",
        purpose: str = "verification",
    ) -> RegisteredCommand:
        normalized_id = (command_id or "").strip()
        if not normalized_id:
            raise ValueError("command_id must not be empty")
        if normalized_id in self._commands:
            raise ValueError(f"command_id already exists: {normalized_id}")
        if isinstance(argv, (str, bytes)):
            raise TypeError("argv must be a sequence, not a command string")
        normalized_argv = tuple(str(item) for item in (argv or ()))
        if not normalized_argv:
            raise ValueError("argv must not be empty")
        if any("\x00" in item for item in normalized_argv):
            raise ValueError("argv must not contain NUL")
        command = RegisteredCommand(
            command_id=normalized_id,
            argv=normalized_argv,
            description=(description or "").strip(),
            purpose=(purpose or "verification").strip() or "verification",
        )
        self._commands[normalized_id] = command
        return command

    def preflight(self, command_id: str) -> RegisteredCommand:
        try:
            return self._commands[command_id]
        except KeyError as exc:
            raise CommandSelectionRejected(
                "command_id_unknown",
                "verify_patch",
                details={"command_id": command_id},
            ) from exc

    def resolve(self, command_id: str) -> tuple[str, ...]:
        return self.preflight(command_id).argv

    def all(self) -> tuple[RegisteredCommand, ...]:
        return tuple(self._commands[key] for key in sorted(self._commands))

    def public_summary(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "command_id": item.command_id,
                "description": item.description,
                "purpose": item.purpose,
            }
            for item in self.all()
        )


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    action_type: str
    description: str
    input_schema: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "description": self.description,
            "inputSchema": dict(self.input_schema),
        }


def _object_schema(properties: dict, required=()) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


class CoreActionCatalog:
    """The model-agnostic public action surface and strict normalizer."""

    _DESCRIPTORS = (
        ActionDescriptor(
            "search_repository",
            "Search the registered repository and return bounded indexed targets.",
            _object_schema(
                {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ("query",),
            ),
        ),
        ActionDescriptor(
            "inspect_repository_target",
            "Inspect a target previously issued by repository search.",
            _object_schema(
                {
                    "inspection_ref": {"type": "string", "minLength": 1},
                    "segment_index": {"type": "integer", "minimum": 1},
                },
                ("inspection_ref",),
            ),
        ),
        ActionDescriptor(
            "focus_target",
            "Select a known target as the current bounded context.",
            _object_schema(
                {
                    "inspection_ref": {"type": "string", "minLength": 1},
                    "segment_index": {"type": "integer", "minimum": 1},
                },
                ("inspection_ref",),
            ),
        ),
        ActionDescriptor(
            "replace_files",
            (
                "Replace complete repository-relative files. Content is the complete "
                "new file content; null deletes a file. Diffs and edit hunks are not accepted."
            ),
            _object_schema(
                {
                    "file_replacements": {
                        "type": "array",
                        "minItems": 1,
                        "items": _object_schema(
                            {
                                "path": {"type": "string", "minLength": 1},
                                "content": {"type": ["string", "null"]},
                            },
                            ("path", "content"),
                        ),
                    },
                    "description": {"type": "string"},
                },
                ("file_replacements",),
            ),
        ),
        ActionDescriptor(
            "verify_patch",
            "Run one host-registered verifier against an applied patch.",
            _object_schema(
                {
                    "patch_id": {"type": "string", "minLength": 1},
                    "command_id": {"type": "string", "minLength": 1},
                    "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                },
                ("patch_id", "command_id"),
            ),
        ),
        ActionDescriptor(
            "accept_patch",
            "Accept an applied patch whose latest verifier passed.",
            _object_schema(
                {"patch_id": {"type": "string", "minLength": 1}},
                ("patch_id",),
            ),
        ),
        ActionDescriptor(
            "rollback_patch",
            "Restore the repository to the frozen pre-patch bytes.",
            _object_schema(
                {"patch_id": {"type": "string", "minLength": 1}},
                ("patch_id",),
            ),
        ),
    )

    def __init__(self) -> None:
        self._by_name = {item.action_type: item for item in self._DESCRIPTORS}

    def descriptors(self) -> tuple[ActionDescriptor, ...]:
        return self._DESCRIPTORS

    def get(self, action_type: str) -> ActionDescriptor:
        try:
            return self._by_name[action_type]
        except KeyError as exc:
            raise ActionPayloadRejected(
                "action_unknown",
                "unknown",
                details={"received_action_type": action_type},
            ) from exc

    def validate(self, action_type: str, payload: Mapping[str, Any]) -> StructuredAction:
        descriptor = self.get(action_type)
        if not isinstance(payload, Mapping):
            raise ActionPayloadRejected("payload_not_object", action_type)
        normalized = dict(payload)
        schema = descriptor.input_schema
        allowed = set(schema["properties"])
        required = set(schema.get("required", ()))
        received = set(normalized)
        missing = sorted(required - received)
        if missing:
            raise ActionPayloadRejected(
                "missing_required_field",
                action_type,
                details={
                    "received_fields": sorted(received),
                    "required_fields": sorted(required),
                    "missing_fields": missing,
                    "allowed_fields": sorted(allowed),
                },
            )
        unknown = sorted(received - allowed)
        if unknown:
            raise ActionPayloadRejected(
                "unknown_field",
                action_type,
                details={
                    "received_fields": sorted(received),
                    "unknown_fields": unknown,
                    "allowed_fields": sorted(allowed),
                },
            )
        validator = getattr(self, f"_validate_{action_type}")
        return StructuredAction(action_type, validator(normalized))

    def _nonempty_string(self, action: str, payload: dict, field: str) -> str:
        value = payload[field]
        if not isinstance(value, str):
            self._invalid_type(action, field, "string", value)
        value = value.strip()
        if not value:
            self._invalid_value(action, field, "must not be empty")
        return value

    def _validate_search_repository(self, payload: dict) -> dict:
        result = {"query": self._nonempty_string("search_repository", payload, "query")}
        limit = payload.get("limit", 8)
        if isinstance(limit, bool) or not isinstance(limit, int):
            self._invalid_type("search_repository", "limit", "integer", limit)
        if not 1 <= limit <= 10:
            self._invalid_value("search_repository", "limit", "must be from 1 to 10")
        result["limit"] = limit
        return result

    def _validate_inspect_repository_target(self, payload: dict) -> dict:
        return self._inspection_payload("inspect_repository_target", payload)

    def _validate_focus_target(self, payload: dict) -> dict:
        return self._inspection_payload("focus_target", payload)

    def _inspection_payload(self, action_type: str, payload: dict) -> dict:
        result = {
            "inspection_ref": self._nonempty_string(
                action_type, payload, "inspection_ref"
            )
        }
        segment_index = payload.get("segment_index")
        if segment_index is not None:
            if (
                isinstance(segment_index, bool)
                or not isinstance(segment_index, int)
            ):
                self._invalid_type(
                    action_type,
                    "segment_index",
                    "integer",
                    segment_index,
                )
            if segment_index < 1:
                self._invalid_value(
                    action_type,
                    "segment_index",
                    "must be positive",
                )
            result["segment_index"] = segment_index
        return result

    def _validate_replace_files(self, payload: dict) -> dict:
        raw = payload["file_replacements"]
        if not isinstance(raw, list):
            self._invalid_type("replace_files", "file_replacements", "array", raw)
        if not raw:
            self._invalid_value("replace_files", "file_replacements", "must not be empty")
        replacements: list[FileReplacement] = []
        seen: set[str] = set()
        for index, item in enumerate(raw):
            if not isinstance(item, Mapping):
                self._invalid_type(
                    "replace_files", f"file_replacements[{index}]", "object", item
                )
            fields = set(item)
            if fields != {"path", "content"}:
                reason = "missing_required_field" if not {"path", "content"}.issubset(fields) else "unknown_field"
                raise ActionPayloadRejected(
                    reason,
                    "replace_files",
                    details={
                        "field_path": f"file_replacements[{index}]",
                        "received_fields": sorted(fields),
                        "required_fields": ["content", "path"],
                        "allowed_fields": ["content", "path"],
                    },
                )
            path = item["path"]
            content = item["content"]
            if not isinstance(path, str):
                self._invalid_type("replace_files", f"file_replacements[{index}].path", "string", path)
            path = path.strip()
            if not path:
                self._invalid_value("replace_files", f"file_replacements[{index}].path", "must not be empty")
            if content is not None and not isinstance(content, str):
                self._invalid_type("replace_files", f"file_replacements[{index}].content", "string or null", content)
            if path in seen:
                raise ActionPayloadRejected(
                    "duplicate_replacement_path",
                    "replace_files",
                    details={"path": path},
                )
            seen.add(path)
            replacements.append(FileReplacement(path, content))
        description = payload.get("description", "")
        if not isinstance(description, str):
            self._invalid_type("replace_files", "description", "string", description)
        return {
            "file_replacements": tuple(replacements),
            "description": description.strip(),
        }

    def _validate_verify_patch(self, payload: dict) -> dict:
        result = {
            "patch_id": self._nonempty_string("verify_patch", payload, "patch_id"),
            "command_id": self._nonempty_string("verify_patch", payload, "command_id"),
        }
        timeout = payload.get("timeout_seconds", 60.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            self._invalid_type("verify_patch", "timeout_seconds", "number", timeout)
        if timeout <= 0:
            self._invalid_value("verify_patch", "timeout_seconds", "must be positive")
        result["timeout_seconds"] = float(timeout)
        return result

    def _validate_accept_patch(self, payload: dict) -> dict:
        return {"patch_id": self._nonempty_string("accept_patch", payload, "patch_id")}

    def _validate_rollback_patch(self, payload: dict) -> dict:
        return {"patch_id": self._nonempty_string("rollback_patch", payload, "patch_id")}

    @staticmethod
    def _received_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, list):
            return "array"
        if isinstance(value, Mapping):
            return "object"
        if isinstance(value, (int, float)):
            return "number"
        return type(value).__name__

    def _invalid_type(self, action: str, field: str, expected: str, value: Any):
        raise ActionPayloadRejected(
            "invalid_field_type",
            action,
            details={
                "field_path": field,
                "expected_type": expected,
                "received_type": self._received_type(value),
            },
        )

    @staticmethod
    def _invalid_value(action: str, field: str, constraint: str):
        raise ActionPayloadRejected(
            "invalid_field_value",
            action,
            details={"field_path": field, "constraint": constraint},
        )
