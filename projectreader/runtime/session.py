"""Minimal standalone ProjectReader runtime.

This module composes repository indexing, controlled inspection, bounded
navigation, transactional patching, trusted verification, and audit logging.
It intentionally owns no model-planning, dynamic-ranking, evaluation, or
transport-presentation state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from projectreader.actions import CommandCatalog, CoreActionCatalog, FileReplacement
from projectreader.actions.rejections import (
    CommandSelectionRejected,
    PatchLifecycleRejected,
    SessionStateRejected,
)
from projectreader.audit import AuditEvent, AuditLedger
from projectreader.navigation import RepositoryNavigator
from projectreader.patching import PatchManager, PatchProposalContext
from projectreader.repository import (
    InspectionRegistry,
    RepositoryIndex,
    RepositoryIndexer,
    RepositoryInspector,
    RepositorySearch,
)


@dataclass(frozen=True, slots=True)
class ProjectReaderStatus:
    """Compact observable state for one standalone repository session."""

    repository_root: str
    indexed: bool
    index_stats: Mapping[str, Any]
    focus: Mapping[str, Any] | None
    context: Mapping[str, Any] | None
    active_patch: Mapping[str, Any] | None
    verifier_configured: bool
    audit: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository_root": self.repository_root,
            "indexed": self.indexed,
            "index_stats": dict(self.index_stats),
            "focus": None if self.focus is None else dict(self.focus),
            "context": None if self.context is None else dict(self.context),
            "active_patch": (
                None if self.active_patch is None else dict(self.active_patch)
            ),
            "verifier_configured": self.verifier_configured,
            "audit": dict(self.audit),
        }


class CoreSession:
    """Authority for one registered repository and its patch lifecycle."""

    DEFAULT_COMMAND_ID = "default"

    def __init__(
        self,
        repository_root: str | Path,
        *,
        verifier: Sequence[str] | None = None,
        max_source_chars: int = 12_000,
        max_working_chars: int = 48_000,
    ) -> None:
        root = Path(repository_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("repository root must be an existing directory")
        if isinstance(max_source_chars, bool) or max_source_chars < 1:
            raise ValueError("max_source_chars must be positive")
        if isinstance(max_working_chars, bool) or max_working_chars < 1:
            raise ValueError("max_working_chars must be positive")

        self.repository_root = root
        self.max_source_chars = int(max_source_chars)
        self.max_working_chars = int(max_working_chars)
        self.audit = AuditLedger()
        self.commands = CommandCatalog()
        self.action_catalog = CoreActionCatalog()
        if verifier is not None:
            self.commands.register(
                self.DEFAULT_COMMAND_ID,
                verifier,
                description="Verifier configured by the repository owner",
            )
        self.patch_manager = PatchManager(root, audit=self.audit)

        self._index: RepositoryIndex | None = None
        self._inspection_registry: InspectionRegistry | None = None
        self._search: RepositorySearch | None = None
        self._inspector: RepositoryInspector | None = None
        self._navigator: RepositoryNavigator | None = None
        self.audit.record("repository_opened", data={"repository_name": root.name})

    @classmethod
    def open(
        cls,
        repository_root: str | Path,
        *,
        verifier: Sequence[str] | None = None,
        max_source_chars: int = 12_000,
        max_working_chars: int = 48_000,
    ) -> "CoreSession":
        return cls(
            repository_root,
            verifier=verifier,
            max_source_chars=max_source_chars,
            max_working_chars=max_working_chars,
        )

    @property
    def repository_index(self) -> RepositoryIndex | None:
        return self._index

    @property
    def navigator(self) -> RepositoryNavigator | None:
        return self._navigator

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return self.audit.all()

    def index(self) -> dict[str, Any]:
        """Build a new index and invalidate repository-scoped inspection refs."""
        index = RepositoryIndexer(
            self.repository_root,
            max_chars=self.max_source_chars,
        ).build()
        registry = InspectionRegistry(index)
        inspector = RepositoryInspector(
            index,
            registry,
            max_inline_chars=self.max_source_chars,
        )
        self._index = index
        self._inspection_registry = registry
        self._search = RepositorySearch(index, registry)
        self._inspector = inspector
        self._navigator = RepositoryNavigator(
            inspector,
            max_chars=self.max_working_chars,
        )
        summary = {
            "repository_name": self.repository_root.name,
            "source_count": len(index.source_store.records),
            "file_count": len(index.files),
            "stats": dict(index.stats),
        }
        self.audit.record("repository_indexed", data=summary)
        return summary

    def search(self, query: str, *, limit: int = 8):
        search = self._require_service(self._search, "search_repository")
        results = search.search(query, limit=limit)
        self.audit.record(
            "repository_searched",
            data={
                "query": query,
                "limit": limit,
                "result_count": len(results),
                "targets": [item.target_ref for item in results],
            },
        )
        return results

    def inspect(self, inspection_ref: str, *, segment_index: int | None = None):
        inspector = self._require_service(
            self._inspector,
            "inspect_repository_target",
        )
        selection = inspector.inspect(inspection_ref, segment_index)
        self.audit.record(
            "repository_target_inspected",
            target_ref=selection.target_ref,
            data={
                "source_id": selection.source_id,
                "target_type": selection.target_type,
                "mode": selection.mode,
                "content_chars": len(selection.content),
                "segment_count": len(selection.segments),
            },
        )
        return selection

    def focus(self, inspection_ref: str, *, segment_index: int | None = None):
        navigator = self._require_service(self._navigator, "focus_target")
        selection = navigator.focus(inspection_ref, segment_index)
        self.audit.record(
            "repository_target_focused",
            target_ref=selection.target_ref,
            data={
                "source_id": selection.source_id,
                "target_type": selection.target_type,
                "mode": selection.mode,
                "working_context": navigator.workspace.summary(),
            },
        )
        return selection

    def patch(
        self,
        file_replacements,
        *,
        description: str = "",
    ):
        normalized = self._normalize_patch_payload(
            file_replacements,
            description=description,
        )
        replacements = normalized["file_replacements"]
        context = self._proposal_context()
        return self.patch_manager.apply(
            normalized["description"],
            {item.path: item.content for item in replacements},
            proposal_context=context,
        )

    def verify(
        self,
        command: Sequence[str] | None = None,
        *,
        patch_id: str | None = None,
        command_id: str | None = None,
        timeout_seconds: float = 60.0,
    ):
        active_id = self._resolve_patch_id(patch_id, "verify_patch")
        resolved_id = command_id
        if command is None:
            resolved_id = command_id or self.DEFAULT_COMMAND_ID
            command = self.commands.resolve(resolved_id)
        return self.patch_manager.verify(
            active_id,
            command,
            command_id=resolved_id,
            timeout_seconds=timeout_seconds,
        )

    def accept(self, *, patch_id: str | None = None):
        return self.patch_manager.accept(
            self._resolve_patch_id(patch_id, "accept_patch")
        )

    def rollback(self, *, patch_id: str | None = None):
        return self.patch_manager.rollback(
            self._resolve_patch_id(patch_id, "rollback_patch")
        )

    def status(self) -> ProjectReaderStatus:
        focus = None
        context = None
        if self._navigator is not None:
            if self._navigator.active is not None:
                focus = asdict(self._navigator.active)
            if self._navigator.context is not None:
                selected = self._navigator.context
                context = {
                    "inspection_ref": selected.inspection_ref,
                    "source_id": selected.source_id,
                    "target_ref": selected.target_ref,
                    "target_type": selected.target_type,
                    "mode": selected.mode,
                    "content_chars": len(selected.content),
                    "segment_count": len(selected.segments),
                    "working_context": self._navigator.workspace.summary(),
                }
        active = self.patch_manager.active_patch()
        active_patch = (
            None
            if active is None
            else active.as_dict(include_verifier_output=False)
        )
        return ProjectReaderStatus(
            repository_root=str(self.repository_root),
            indexed=self._index is not None,
            index_stats={} if self._index is None else dict(self._index.stats),
            focus=focus,
            context=context,
            active_patch=active_patch,
            verifier_configured=bool(self.commands.all()),
            audit=self.audit.summary(),
        )

    def execute(self, action_type: str, payload: Mapping[str, Any]):
        """Run one strict Core action through the shared action executor."""
        from projectreader.actions.executor import CoreActionExecutor

        return CoreActionExecutor(self, catalog=self.action_catalog).execute(
            action_type,
            payload,
        )

    def _normalize_patch_payload(self, values, *, description: str) -> Mapping[str, Any]:
        if isinstance(values, Mapping):
            values = [
                {"path": str(path), "content": content}
                for path, content in values.items()
            ]
        elif not isinstance(values, (str, bytes)):
            try:
                values = [
                    item.as_dict() if isinstance(item, FileReplacement) else item
                    for item in values
                ]
            except TypeError:
                # Preserve the invalid value so the shared Action Catalog emits
                # the closed, machine-readable payload rejection contract.
                pass
        normalized = self.action_catalog.validate(
            "replace_files",
            {
                "file_replacements": values,
                "description": description,
            },
        )
        return normalized.payload

    def _proposal_context(self) -> PatchProposalContext:
        if self._navigator is None or self._navigator.active is None:
            return PatchProposalContext()
        active = self._navigator.active
        context = self._navigator.context
        return PatchProposalContext(
            focus_target_ref=active.target_ref,
            source_id=active.source_id,
            context_mode=None if context is None else context.mode,
        )

    def _resolve_patch_id(self, patch_id: str | None, action_type: str) -> str:
        if patch_id is not None:
            return patch_id
        active_id = self.patch_manager.active_patch_id
        if active_id is None:
            raise PatchLifecycleRejected(
                "patch_not_applied",
                action_type,
                details={"patch_id": None, "status": "none"},
            )
        return active_id

    @staticmethod
    def _require_service(service, action_type: str):
        if service is None:
            raise SessionStateRejected("repository_not_indexed", action_type)
        return service


class ProjectReader(CoreSession):
    """Small stable public facade for ProjectReader Core."""
