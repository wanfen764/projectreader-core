"""Exception-compensated file transactions scoped to one repository root."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping

from projectreader.actions.rejections import WorkspaceChangeRejected


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    relative_path: str
    existed: bool
    content: bytes | None


@dataclass(frozen=True, slots=True)
class FrozenWorkspaceChange:
    """Immutable original and desired bytes frozen before mutation."""

    repository_relative_path: str
    change_kind: str
    original_bytes: bytes | None
    desired_bytes: bytes | None

    def __post_init__(self) -> None:
        if not self.repository_relative_path:
            raise ValueError("frozen workspace change path must not be empty")
        if self.change_kind != self.classify(self.original_bytes, self.desired_bytes):
            raise ValueError("frozen workspace change kind is inconsistent")

    @staticmethod
    def classify(original_bytes: bytes | None, desired_bytes: bytes | None) -> str:
        if original_bytes is None and desired_bytes is not None:
            return "added"
        if original_bytes is not None and desired_bytes is None:
            return "deleted"
        if original_bytes == desired_bytes:
            return "unchanged"
        return "modified"


@dataclass(frozen=True, slots=True)
class WorkspacePathPlan:
    relative_path: str
    resolved_path: Path
    existed: bool


@dataclass(frozen=True, slots=True)
class WorkspaceChangePlan:
    root: Path
    paths: tuple[WorkspacePathPlan, ...]

    def path_for(self, relative_path: str) -> Path:
        for item in self.paths:
            if item.relative_path == relative_path:
                return item.resolved_path
        raise KeyError(relative_path)


class WorkspaceTransactionState(str, Enum):
    NEW = "new"
    PREPARED = "prepared"
    APPLYING = "applying"
    APPLIED = "applied"
    COMPENSATING = "compensating"
    APPLY_FAILED_RESTORED = "apply_failed_restored"
    APPLY_FAILED_PARTIAL = "apply_failed_partial"
    RESTORING = "restoring"
    RESTORED = "restored"
    RESTORE_FAILED_PARTIAL = "restore_failed_partial"


class FileApplyProgress(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    ATTEMPTED = "attempted"
    COMPLETED = "completed"


class FileRecoveryProgress(str, Enum):
    NOT_ATTEMPTED = "not_attempted"
    ATTEMPTED = "attempted"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(slots=True)
class _PreparedWorkspaceOperation:
    relative_path: str
    resolved_path: Path
    desired_content: bytes | None
    snapshot: FileSnapshot
    apply_progress: FileApplyProgress = FileApplyProgress.NOT_ATTEMPTED
    compensation_progress: FileRecoveryProgress = FileRecoveryProgress.NOT_ATTEMPTED
    restore_progress: FileRecoveryProgress = FileRecoveryProgress.NOT_ATTEMPTED


@dataclass(frozen=True, slots=True)
class WorkspaceOperationProgress:
    relative_path: str
    apply_progress: str
    compensation_progress: str
    restore_progress: str


@dataclass(frozen=True, slots=True)
class WorkspaceTransactionReport:
    transaction_state: str
    attempted_paths: tuple[str, ...]
    completed_paths: tuple[str, ...]
    recovered_paths: tuple[str, ...]
    recovery_failed_paths: tuple[str, ...]
    created_directories: tuple[str, ...]
    removed_directories: tuple[str, ...]
    directory_recovery_failed_paths: tuple[str, ...]
    repository_restored: bool

    def as_dict(self) -> dict:
        return {
            "transaction_state": self.transaction_state,
            "attempted_paths": list(self.attempted_paths),
            "completed_paths": list(self.completed_paths),
            "recovered_paths": list(self.recovered_paths),
            "recovery_failed_paths": list(self.recovery_failed_paths),
            "created_directories": list(self.created_directories),
            "removed_directories": list(self.removed_directories),
            "directory_recovery_failed_paths": list(
                self.directory_recovery_failed_paths
            ),
            "repository_restored": self.repository_restored,
        }


class WorkspaceTransactionApplyError(RuntimeError):
    """Apply failed; ``report`` describes compensation truthfully."""

    def __init__(self, report: WorkspaceTransactionReport):
        self.report = report
        super().__init__("workspace transaction apply failed")


class WorkspaceTransactionRestoreError(RuntimeError):
    """Explicit rollback failed after at least one restore attempt."""

    def __init__(self, report: WorkspaceTransactionReport):
        self.report = report
        super().__init__("workspace transaction restore failed")


class WorkspaceTransaction:
    """Prepare, apply, compensate, and restore a bounded file set.

    This is exception compensation rather than filesystem-level atomicity.  It
    does not provide a crash-durable journal or protect against concurrent
    external mutations.
    """

    def __init__(self, root_path: str | Path):
        self.root = Path(root_path).resolve()
        if not self.root.is_dir():
            raise ValueError("repository root must be an existing directory")
        self.state = WorkspaceTransactionState.NEW
        self._plan: WorkspaceChangePlan | None = None
        self._operations: list[_PreparedWorkspaceOperation] = []
        self._snapshots: dict[str, FileSnapshot] = {}
        self._created_directories: list[Path] = []
        self._removed_directories: list[Path] = []
        self._directory_recovery_failures: list[Path] = []

    def preflight_changes(
        self, changes: Mapping[str, str | bytes | None]
    ) -> WorkspaceChangePlan:
        """Build a path plan without reading contents or mutating the tree."""
        if not isinstance(changes, Mapping) or not changes:
            raise WorkspaceChangeRejected("replacement_set_empty", "replace_files")
        paths: list[WorkspacePathPlan] = []
        for relative_path in sorted(changes):
            path = self._resolve_inside_root(relative_path)
            existed = path.exists()
            if existed and not path.is_file():
                raise WorkspaceChangeRejected(
                    "target_is_directory",
                    "replace_files",
                    details={"path": relative_path},
                )
            paths.append(WorkspacePathPlan(relative_path, path, existed))
        return WorkspaceChangePlan(self.root, tuple(paths))

    def prepare(self, changes: Mapping[str, str | bytes | None]) -> WorkspaceChangePlan:
        """Freeze all desired/original bytes before the first mutation."""
        if self.state in {
            WorkspaceTransactionState.RESTORED,
            WorkspaceTransactionState.APPLY_FAILED_RESTORED,
        }:
            self._reset_for_prepare()
        if self.state is not WorkspaceTransactionState.NEW:
            raise RuntimeError("workspace transaction is not ready to prepare")

        plan = self.preflight_changes(changes)
        desired = [
            (relative_path, self._freeze_desired_content(new_content))
            for relative_path, new_content in changes.items()
        ]
        snapshots: dict[str, FileSnapshot] = {}
        for path_plan in plan.paths:
            snapshots[path_plan.relative_path] = FileSnapshot(
                relative_path=path_plan.relative_path,
                existed=path_plan.existed,
                content=(path_plan.resolved_path.read_bytes() if path_plan.existed else None),
            )
        operations = [
            _PreparedWorkspaceOperation(
                relative_path=relative_path,
                resolved_path=plan.path_for(relative_path),
                desired_content=new_content,
                snapshot=snapshots[relative_path],
            )
            for relative_path, new_content in desired
        ]
        self._plan = plan
        self._snapshots = snapshots
        self._operations = operations
        self._created_directories = []
        self._removed_directories = []
        self._directory_recovery_failures = []
        self.state = WorkspaceTransactionState.PREPARED
        return plan

    def apply(self, changes: Mapping[str, str | bytes | None]):
        self.prepare(changes)
        return self.apply_prepared()

    def apply_prepared(self) -> list[FileSnapshot]:
        if self.state is not WorkspaceTransactionState.PREPARED:
            raise RuntimeError("workspace transaction has not been prepared")
        self.state = WorkspaceTransactionState.APPLYING
        try:
            for operation in self._operations:
                operation.apply_progress = FileApplyProgress.ATTEMPTED
                self._apply_operation(operation)
                operation.apply_progress = FileApplyProgress.COMPLETED
        except Exception as exc:
            self.state = WorkspaceTransactionState.COMPENSATING
            restored = self._compensate_attempted_operations()
            self.state = (
                WorkspaceTransactionState.APPLY_FAILED_RESTORED
                if restored
                else WorkspaceTransactionState.APPLY_FAILED_PARTIAL
            )
            raise WorkspaceTransactionApplyError(self.report()) from exc
        self.state = WorkspaceTransactionState.APPLIED
        return list(self._snapshots.values())

    def restore(self) -> list[FileSnapshot]:
        if self.state in {
            WorkspaceTransactionState.NEW,
            WorkspaceTransactionState.PREPARED,
            WorkspaceTransactionState.APPLY_FAILED_RESTORED,
            WorkspaceTransactionState.RESTORED,
        }:
            return []
        if self.state in {
            WorkspaceTransactionState.APPLY_FAILED_PARTIAL,
            WorkspaceTransactionState.RESTORE_FAILED_PARTIAL,
        }:
            raise RuntimeError(
                "partial workspace recovery requires an explicit recovery policy"
            )
        if self.state is not WorkspaceTransactionState.APPLIED:
            raise RuntimeError("workspace transaction cannot be restored in this state")
        self.state = WorkspaceTransactionState.RESTORING
        restored, failed = self._restore_operations(
            "restore_progress", reversed(self._operations)
        )
        directories_restored = self._remove_created_directories()
        if failed or not directories_restored:
            self.state = WorkspaceTransactionState.RESTORE_FAILED_PARTIAL
            raise WorkspaceTransactionRestoreError(self.report())
        self.state = WorkspaceTransactionState.RESTORED
        return [self._snapshots[path] for path in restored]

    def frozen_changes(self) -> tuple[FrozenWorkspaceChange, ...]:
        if self.state is WorkspaceTransactionState.NEW or self._plan is None:
            raise RuntimeError("workspace transaction has no frozen changes")
        return tuple(
            FrozenWorkspaceChange(
                operation.relative_path,
                FrozenWorkspaceChange.classify(
                    operation.snapshot.content if operation.snapshot.existed else None,
                    operation.desired_content,
                ),
                operation.snapshot.content if operation.snapshot.existed else None,
                operation.desired_content,
            )
            for operation in self._operations
        )

    def operation_progress(self) -> tuple[WorkspaceOperationProgress, ...]:
        return tuple(
            WorkspaceOperationProgress(
                operation.relative_path,
                operation.apply_progress.value,
                operation.compensation_progress.value,
                operation.restore_progress.value,
            )
            for operation in self._operations
        )

    def report(self) -> WorkspaceTransactionReport:
        compensation_states = {
            WorkspaceTransactionState.COMPENSATING,
            WorkspaceTransactionState.APPLY_FAILED_RESTORED,
            WorkspaceTransactionState.APPLY_FAILED_PARTIAL,
        }
        attribute = (
            "compensation_progress"
            if self.state in compensation_states
            else "restore_progress"
        )
        recovered: list[str] = []
        failed: list[str] = []
        for operation in self._operations:
            progress = getattr(operation, attribute)
            if progress is FileRecoveryProgress.COMPLETED:
                recovered.append(operation.relative_path)
            elif progress is FileRecoveryProgress.FAILED:
                failed.append(operation.relative_path)
        directory_failures = self._relative_directories(
            self._directory_recovery_failures
        )
        return WorkspaceTransactionReport(
            transaction_state=self.state.value,
            attempted_paths=tuple(
                item.relative_path
                for item in self._operations
                if item.apply_progress is not FileApplyProgress.NOT_ATTEMPTED
            ),
            completed_paths=tuple(
                item.relative_path
                for item in self._operations
                if item.apply_progress is FileApplyProgress.COMPLETED
            ),
            recovered_paths=tuple(recovered),
            recovery_failed_paths=tuple(sorted(set(failed) | set(directory_failures))),
            created_directories=self._relative_directories(self._created_directories),
            removed_directories=self._relative_directories(self._removed_directories),
            directory_recovery_failed_paths=directory_failures,
            repository_restored=self.state
            in {
                WorkspaceTransactionState.NEW,
                WorkspaceTransactionState.PREPARED,
                WorkspaceTransactionState.APPLY_FAILED_RESTORED,
                WorkspaceTransactionState.RESTORED,
            },
        )

    @staticmethod
    def _freeze_desired_content(content: str | bytes | None) -> bytes | None:
        if content is None:
            return None
        if isinstance(content, bytes):
            return bytes(content)
        if isinstance(content, str):
            return content.encode("utf-8")
        raise TypeError("workspace change content must be str, bytes, or None")

    def _apply_operation(self, operation: _PreparedWorkspaceOperation) -> None:
        path = operation.resolved_path
        if operation.desired_content is None:
            if path.exists():
                path.unlink()
            return
        self._ensure_parent_directories(path.parent)
        path.write_bytes(operation.desired_content)

    def _ensure_parent_directories(self, parent: Path) -> None:
        missing: list[Path] = []
        cursor = parent
        while cursor != self.root and not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        for directory in reversed(missing):
            try:
                directory.mkdir()
            except FileExistsError:
                if not directory.is_dir():
                    raise
            except Exception:
                if directory.is_dir() and directory not in self._created_directories:
                    self._created_directories.append(directory)
                raise
            else:
                self._created_directories.append(directory)

    def _compensate_attempted_operations(self) -> bool:
        attempted = [
            item
            for item in self._operations
            if item.apply_progress is not FileApplyProgress.NOT_ATTEMPTED
        ]
        _restored, failed = self._restore_operations(
            "compensation_progress", reversed(attempted)
        )
        return not failed and self._remove_created_directories()

    def _restore_operations(
        self, progress_attribute: str, operations: Iterable[_PreparedWorkspaceOperation]
    ) -> tuple[list[str], list[str]]:
        restored: list[str] = []
        failed: list[str] = []
        for operation in operations:
            setattr(operation, progress_attribute, FileRecoveryProgress.ATTEMPTED)
            try:
                self._restore_snapshot(operation)
            except Exception:
                setattr(operation, progress_attribute, FileRecoveryProgress.FAILED)
                failed.append(operation.relative_path)
            else:
                setattr(operation, progress_attribute, FileRecoveryProgress.COMPLETED)
                restored.append(operation.relative_path)
        return restored, failed

    @staticmethod
    def _restore_snapshot(operation: _PreparedWorkspaceOperation) -> None:
        path = operation.resolved_path
        snapshot = operation.snapshot
        if snapshot.existed:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(snapshot.content or b"")
            return
        if not path.exists():
            return
        if not path.is_file():
            raise RuntimeError(
                f"workspace path became a non-file during recovery: {operation.relative_path}"
            )
        path.unlink()

    def _remove_created_directories(self) -> bool:
        self._removed_directories = []
        self._directory_recovery_failures = []
        for directory in reversed(self._created_directories):
            try:
                directory.rmdir()
            except FileNotFoundError:
                self._removed_directories.append(directory)
            except Exception:
                self._directory_recovery_failures.append(directory)
            else:
                self._removed_directories.append(directory)
        return not self._directory_recovery_failures

    def _relative_directories(self, directories: Iterable[Path]) -> tuple[str, ...]:
        return tuple(item.relative_to(self.root).as_posix() for item in directories)

    def _reset_for_prepare(self) -> None:
        self.state = WorkspaceTransactionState.NEW
        self._plan = None
        self._operations = []
        self._snapshots = {}
        self._created_directories = []
        self._removed_directories = []
        self._directory_recovery_failures = []

    def _resolve_inside_root(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise WorkspaceChangeRejected(
                "path_not_repository_relative",
                "replace_files",
                details={"path": relative_path},
            )
        relative = Path(relative_path)
        if relative.is_absolute():
            raise WorkspaceChangeRejected(
                "path_not_repository_relative",
                "replace_files",
                details={"path": relative_path},
            )
        resolved = (self.root / relative).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceChangeRejected(
                "path_outside_repository",
                "replace_files",
                details={"path": relative_path},
            ) from exc
        return resolved
