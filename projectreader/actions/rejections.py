"""Machine-readable, fail-closed rejection contracts for public Core actions."""

from __future__ import annotations

from typing import ClassVar


class ProjectReaderRejected(ValueError):
    """Base class for a caller-correctable, side-effect-free rejection.

    Concrete subclasses use a closed reason/action table.  Merely inheriting
    from this class does not make an unknown error safe or recoverable at an
    MCP boundary.
    """

    ERROR_TYPE: ClassVar[str] = ""
    REASON_ACTIONS: ClassVar[dict[str, frozenset[str]]] = {}
    REASON_MESSAGES: ClassVar[dict[str, str]] = {}

    def __init__(
        self,
        reason_code: str,
        action_type: str,
        *,
        message: str | None = None,
        details: dict | None = None,
    ) -> None:
        actions = self.REASON_ACTIONS.get(reason_code)
        if actions is None:
            raise ValueError(
                f"unknown {type(self).__name__} reason code: {reason_code}"
            )
        if action_type not in actions:
            raise ValueError(
                f"reason code {reason_code} does not apply to {action_type}"
            )
        self.reason_code = reason_code
        self.action_type = action_type
        self.details = dict(details or {})
        super().__init__(message or self.REASON_MESSAGES[reason_code])

    @property
    def error_type(self) -> str:
        return self.ERROR_TYPE

    def as_error(self) -> dict:
        result = {
            "error_type": self.ERROR_TYPE,
            "reason_code": self.reason_code,
            "action_type": self.action_type,
            "recoverable": True,
            "side_effects": False,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


_ALL_CORE_ACTIONS = frozenset(
    {
        "search_repository",
        "inspect_repository_target",
        "focus_target",
        "replace_files",
        "verify_patch",
        "accept_patch",
        "rollback_patch",
    }
)


class ActionPayloadRejected(ProjectReaderRejected):
    ERROR_TYPE = "action_payload_validation"
    REASON_ACTIONS = {
        "action_unknown": _ALL_CORE_ACTIONS | frozenset({"unknown"}),
        "payload_not_object": _ALL_CORE_ACTIONS,
        "missing_required_field": _ALL_CORE_ACTIONS,
        "unknown_field": _ALL_CORE_ACTIONS,
        "invalid_field_type": _ALL_CORE_ACTIONS,
        "invalid_field_value": _ALL_CORE_ACTIONS,
        "duplicate_replacement_path": frozenset({"replace_files"}),
    }
    REASON_MESSAGES = {
        "action_unknown": "The requested action is not registered",
        "payload_not_object": "The action payload must be an object",
        "missing_required_field": "The action payload is missing a required field",
        "unknown_field": "The action payload contains an unknown field",
        "invalid_field_type": "An action payload field has an invalid type",
        "invalid_field_value": "An action payload field has an invalid value",
        "duplicate_replacement_path": "Replacement paths must be unique",
    }


class WorkspaceChangeRejected(ProjectReaderRejected):
    ERROR_TYPE = "workspace_change_precondition"
    REASON_ACTIONS = {
        "path_not_repository_relative": frozenset({"replace_files"}),
        "path_outside_repository": frozenset({"replace_files"}),
        "target_is_directory": frozenset({"replace_files"}),
        "replacement_set_empty": frozenset({"replace_files"}),
    }
    REASON_MESSAGES = {
        "path_not_repository_relative": "Changes require repository-relative paths",
        "path_outside_repository": "Changes cannot escape the repository root",
        "target_is_directory": "Changes can target files only",
        "replacement_set_empty": "At least one file replacement is required",
    }


class PatchLifecycleRejected(ProjectReaderRejected):
    ERROR_TYPE = "patch_lifecycle_precondition"
    REASON_ACTIONS = {
        "active_patch_in_progress": frozenset({"replace_files"}),
        "patch_unknown": frozenset(
            {"verify_patch", "accept_patch", "rollback_patch"}
        ),
        "patch_not_applied": frozenset(
            {"verify_patch", "accept_patch", "rollback_patch"}
        ),
        "patch_verification_required": frozenset({"accept_patch"}),
        "patch_verification_not_passed": frozenset({"accept_patch"}),
    }
    REASON_MESSAGES = {
        "active_patch_in_progress": (
            "The active patch must be accepted or rolled back before another patch"
        ),
        "patch_unknown": "The patch identifier is not registered",
        "patch_not_applied": "This action requires an applied patch",
        "patch_verification_required": "The patch must be verified before acceptance",
        "patch_verification_not_passed": (
            "The latest verification must pass before acceptance"
        ),
    }


class CommandSelectionRejected(ProjectReaderRejected):
    ERROR_TYPE = "command_selection_precondition"
    REASON_ACTIONS = {
        "command_id_unknown": frozenset({"verify_patch"}),
    }
    REASON_MESSAGES = {
        "command_id_unknown": "The command_id is not registered",
    }


class SessionStateRejected(ProjectReaderRejected):
    """A Core operation is valid but unavailable in the current session state."""

    ERROR_TYPE = "core_session_precondition"
    REASON_ACTIONS = {
        "repository_not_indexed": frozenset(
            {"search_repository", "inspect_repository_target", "focus_target"}
        ),
    }
    REASON_MESSAGES = {
        "repository_not_indexed": "The repository must be indexed before this action",
    }
