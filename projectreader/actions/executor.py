"""Single structured-action dispatch path for ProjectReader Core."""

from __future__ import annotations

from typing import Any, Mapping

from projectreader.repository.errors import RepositoryRejected

from .catalog import CoreActionCatalog
from .models import ActionResult
from .rejections import ProjectReaderRejected


class CoreActionExecutor:
    """Validate through ``CoreActionCatalog`` and call one CoreSession."""

    def __init__(self, session, *, catalog: CoreActionCatalog | None = None):
        self.session = session
        self.catalog = catalog or CoreActionCatalog()

    def execute(self, action_type: str, payload: Mapping[str, Any]) -> ActionResult:
        try:
            action = self.catalog.validate(action_type, payload)
            data = self._dispatch(action.action_type, action.payload)
        except ProjectReaderRejected as exc:
            return ActionResult(action_type, "rejected", error=exc.as_error())
        except RepositoryRejected as exc:
            error = exc.structured()
            error["action_type"] = error.pop("action")
            return ActionResult(action_type, "rejected", error=error)
        return ActionResult(action.action_type, "executed", data={"result": data})

    def _dispatch(self, action_type: str, payload: Mapping[str, Any]):
        if action_type == "search_repository":
            return self.session.search(payload["query"], limit=payload["limit"])
        if action_type == "inspect_repository_target":
            return self.session.inspect(
                payload["inspection_ref"],
                segment_index=payload.get("segment_index"),
            )
        if action_type == "focus_target":
            return self.session.focus(
                payload["inspection_ref"],
                segment_index=payload.get("segment_index"),
            )
        if action_type == "replace_files":
            return self.session.patch(
                payload["file_replacements"],
                description=payload["description"],
            )
        if action_type == "verify_patch":
            return self.session.verify(
                patch_id=payload["patch_id"],
                command_id=payload["command_id"],
                timeout_seconds=payload["timeout_seconds"],
            )
        if action_type == "accept_patch":
            return self.session.accept(patch_id=payload["patch_id"])
        if action_type == "rollback_patch":
            return self.session.rollback(patch_id=payload["patch_id"])
        raise AssertionError("validated Core action has no dispatcher")
