"""Value objects shared by Core action transports and runtime dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class FileReplacement:
    """A complete file replacement; ``None`` means delete the file."""

    path: str
    content: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path.strip():
            raise ValueError("replacement path must be a non-empty string")
        if self.content is not None and not isinstance(self.content, str):
            raise TypeError("replacement content must be a string or null")

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "content": self.content}


@dataclass(frozen=True, slots=True)
class StructuredAction:
    action_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"action_type": self.action_type, "payload": dict(self.payload)}


@dataclass(frozen=True, slots=True)
class ActionResult:
    action_type: str
    status: str
    data: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "executed"

    def as_dict(self) -> dict[str, Any]:
        result = {
            "action_type": self.action_type,
            "status": self.status,
            "data": dict(self.data),
        }
        if self.error is not None:
            result["error"] = dict(self.error)
        return result
