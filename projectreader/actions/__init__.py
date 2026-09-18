"""Structured action contracts for ProjectReader Core."""

from .catalog import (
    ActionDescriptor,
    CommandCatalog,
    CoreActionCatalog,
    RegisteredCommand,
)
from .models import ActionResult, FileReplacement, StructuredAction
from .rejections import (
    ActionPayloadRejected,
    CommandSelectionRejected,
    PatchLifecycleRejected,
    ProjectReaderRejected,
    SessionStateRejected,
    WorkspaceChangeRejected,
)

__all__ = [
    "ActionDescriptor",
    "ActionPayloadRejected",
    "ActionResult",
    "CommandCatalog",
    "CommandSelectionRejected",
    "CoreActionCatalog",
    "FileReplacement",
    "PatchLifecycleRejected",
    "ProjectReaderRejected",
    "RegisteredCommand",
    "SessionStateRejected",
    "StructuredAction",
    "WorkspaceChangeRejected",
]
