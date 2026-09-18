"""Controlled patch, verification, acceptance, and rollback lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import time
from typing import Mapping, Sequence

from projectreader.actions.rejections import (
    PatchLifecycleRejected,
    WorkspaceChangeRejected,
)
from projectreader.audit import AuditLedger

from .attribution import (
    PatchAttributionAnalyzer,
    PatchChangedScope,
    PatchProposalContext,
)
from .transaction import (
    WorkspaceTransaction,
    WorkspaceTransactionApplyError,
    WorkspaceTransactionRestoreError,
    WorkspaceTransactionState,
)


@dataclass(slots=True)
class PatchVerification:
    command_id: str | None
    command: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.returncode == 0

    def as_dict(self, *, include_output: bool = True) -> dict:
        result = {
            "command_id": self.command_id,
            "command": list(self.command),
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "duration_seconds": self.duration_seconds,
            "passed": self.passed,
        }
        if include_output:
            result.update({"stdout": self.stdout, "stderr": self.stderr})
        return result


@dataclass(slots=True)
class PatchRecord:
    patch_id: str
    description: str
    proposal_context: PatchProposalContext
    changed_files: tuple[str, ...]
    changed_scopes: tuple[PatchChangedScope, ...]
    causal_attribution: None = None
    status: str = "prepared"
    verifications: list[PatchVerification] = field(default_factory=list)

    def attribution_payload(self) -> dict:
        return {
            "proposal_context": self.proposal_context.as_dict(),
            "changed_files": list(self.changed_files),
            "changed_scopes": [item.as_dict() for item in self.changed_scopes],
            "causal_attribution": self.causal_attribution,
        }

    def as_dict(self, *, include_verifier_output: bool = True) -> dict:
        return {
            "patch_id": self.patch_id,
            "description": self.description,
            **self.attribution_payload(),
            "status": self.status,
            "verifications": [
                item.as_dict(include_output=include_verifier_output)
                for item in self.verifications
            ],
        }


class PatchApplyFailed(RuntimeError):
    """A patch transaction failed; its report describes compensation state."""

    def __init__(self, record: PatchRecord, transaction, report):
        self.record = record
        self.transaction = transaction
        self.report = report
        super().__init__("patch apply failed")


class PatchRollbackFailed(RuntimeError):
    """An explicit rollback left the repository partially restored."""

    def __init__(self, record: PatchRecord, transaction, report):
        self.record = record
        self.transaction = transaction
        self.report = report
        super().__init__("patch rollback failed")


class PatchStore:
    def __init__(self) -> None:
        self._records: dict[str, PatchRecord] = {}
        self._transactions: dict[str, WorkspaceTransaction] = {}
        self._next_sequence = 1

    def create(
        self,
        description: str,
        changes: Mapping[str, str | bytes | None],
        *,
        proposal_context: PatchProposalContext,
        changed_scopes: tuple[PatchChangedScope, ...],
    ) -> PatchRecord:
        patch_id = f"patch-{self._next_sequence:06d}"
        self._next_sequence += 1
        record = PatchRecord(
            patch_id=patch_id,
            description=(description or "").strip(),
            proposal_context=proposal_context,
            changed_files=tuple(sorted(changes)),
            changed_scopes=tuple(changed_scopes),
        )
        self._records[patch_id] = record
        return record

    def attach_transaction(
        self, patch_id: str, transaction: WorkspaceTransaction
    ) -> None:
        self._transactions[patch_id] = transaction

    def transaction(self, patch_id: str) -> WorkspaceTransaction | None:
        return self._transactions.get(patch_id)

    def get(self, patch_id: str, *, action_type: str) -> PatchRecord:
        try:
            return self._records[patch_id]
        except KeyError as exc:
            raise PatchLifecycleRejected(
                "patch_unknown",
                action_type,
                details={"patch_id": patch_id},
            ) from exc

    def history(self) -> tuple[PatchRecord, ...]:
        return tuple(self._records[key] for key in self._records)


class PatchManager:
    """Safe in-memory patch lifecycle over ``WorkspaceTransaction``.

    The manager permits one applied patch at a time. Verification commands are
    exact argv sequences and always execute with ``shell=False``. The caller is
    expected to resolve model-facing command identifiers through CommandCatalog.
    """

    def __init__(
        self,
        repository_root: str | Path,
        *,
        store: PatchStore | None = None,
        attribution_analyzer: PatchAttributionAnalyzer | None = None,
        audit: AuditLedger | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        if not self.repository_root.is_dir():
            raise ValueError("repository root must be an existing directory")
        self.store = store or PatchStore()
        self.attribution_analyzer = attribution_analyzer or PatchAttributionAnalyzer()
        self.audit = audit or AuditLedger()
        self._active_patch_id: str | None = None

    @property
    def active_patch_id(self) -> str | None:
        return self._active_patch_id

    def active_patch(self) -> PatchRecord | None:
        if self._active_patch_id is None:
            return None
        return self.store.get(self._active_patch_id, action_type="verify_patch")

    def apply(
        self,
        description: str,
        changes: Mapping[str, str | bytes | None],
        *,
        proposal_context: PatchProposalContext | None = None,
    ) -> PatchRecord:
        """Freeze facts, apply complete-file changes, then record the result."""
        if self._active_patch_id is not None:
            raise PatchLifecycleRejected(
                "active_patch_in_progress",
                "replace_files",
                details={"patch_id": self._active_patch_id},
            )
        if not isinstance(changes, Mapping) or not changes:
            raise WorkspaceChangeRejected("replacement_set_empty", "replace_files")
        transaction = WorkspaceTransaction(self.repository_root)
        # Preparation, attribution, and all recoverable validation happen before
        # patch identity allocation, audit writes, or repository mutation.
        transaction.prepare(changes)
        context = proposal_context or PatchProposalContext()
        changed_scopes = self.attribution_analyzer.analyze(
            transaction.frozen_changes()
        )
        record = self.store.create(
            description,
            changes,
            proposal_context=context,
            changed_scopes=changed_scopes,
        )
        self.store.attach_transaction(record.patch_id, transaction)
        self._active_patch_id = record.patch_id
        record.status = "applying"
        try:
            transaction.apply_prepared()
        except WorkspaceTransactionApplyError as exc:
            record.status = (
                "apply_failed_restored"
                if transaction.state
                is WorkspaceTransactionState.APPLY_FAILED_RESTORED
                else "apply_failed_partial"
            )
            self.audit.record(
                "patch_apply_failed",
                target_ref=record.patch_id,
                data={
                    "patch_id": record.patch_id,
                    "status": record.status,
                    "transaction": exc.report.as_dict(),
                    "patch_attribution": record.attribution_payload(),
                },
            )
            if transaction.state is WorkspaceTransactionState.APPLY_FAILED_RESTORED:
                self._active_patch_id = None
            raise PatchApplyFailed(record, transaction, exc.report) from exc
        record.status = "applied"
        self.audit.record(
            "patch_applied",
            target_ref=record.patch_id,
            data={
                "patch_id": record.patch_id,
                "patch_attribution": record.attribution_payload(),
            },
        )
        return record

    def verify(
        self,
        patch_id: str,
        command: Sequence[str],
        *,
        command_id: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> PatchVerification:
        record = self._require_applied(patch_id, "verify_patch")
        argv = self._normalize_argv(command)
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        start = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=self.repository_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(timeout_seconds),
                shell=False,
                check=False,
            )
            verification = PatchVerification(
                command_id=command_id,
                command=argv,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timed_out=False,
                duration_seconds=time.monotonic() - start,
            )
        except subprocess.TimeoutExpired as exc:
            verification = PatchVerification(
                command_id=command_id,
                command=argv,
                returncode=None,
                stdout=self._coerce_text(exc.stdout),
                stderr=self._coerce_text(exc.stderr),
                timed_out=True,
                duration_seconds=time.monotonic() - start,
            )
        record.verifications.append(verification)
        self.audit.record(
            "patch_verified",
            target_ref=record.patch_id,
            data={
                "patch_id": record.patch_id,
                "command_id": command_id,
                "returncode": verification.returncode,
                "timed_out": verification.timed_out,
                "passed": verification.passed,
                "patch_attribution": record.attribution_payload(),
            },
        )
        return verification

    def accept(self, patch_id: str) -> PatchRecord:
        record = self._require_applied(patch_id, "accept_patch")
        if not record.verifications:
            raise PatchLifecycleRejected(
                "patch_verification_required",
                "accept_patch",
                details={"patch_id": patch_id},
            )
        if not record.verifications[-1].passed:
            raise PatchLifecycleRejected(
                "patch_verification_not_passed",
                "accept_patch",
                details={"patch_id": patch_id},
            )
        record.status = "accepted"
        self._active_patch_id = None
        self.audit.record(
            "patch_accepted",
            target_ref=record.patch_id,
            data={
                "patch_id": record.patch_id,
                "patch_attribution": record.attribution_payload(),
            },
        )
        return record

    def rollback(self, patch_id: str) -> PatchRecord:
        record = self._require_applied(patch_id, "rollback_patch")
        transaction = self.store.transaction(patch_id)
        if transaction is None:
            raise RuntimeError("patch is missing its workspace transaction")
        try:
            transaction.restore()
        except WorkspaceTransactionRestoreError as exc:
            record.status = "rollback_failed_partial"
            self.audit.record(
                "patch_rollback_failed",
                target_ref=record.patch_id,
                data={
                    "patch_id": patch_id,
                    "transaction": exc.report.as_dict(),
                    "patch_attribution": record.attribution_payload(),
                },
            )
            raise PatchRollbackFailed(record, transaction, exc.report) from exc
        record.status = "rolled_back"
        self._active_patch_id = None
        self.audit.record(
            "patch_rolled_back",
            target_ref=record.patch_id,
            data={
                "patch_id": record.patch_id,
                "patch_attribution": record.attribution_payload(),
            },
        )
        return record

    def _require_applied(self, patch_id: str, action_type: str) -> PatchRecord:
        record = self.store.get(patch_id, action_type=action_type)
        if record.status != "applied" or self._active_patch_id != patch_id:
            raise PatchLifecycleRejected(
                "patch_not_applied",
                action_type,
                details={"patch_id": patch_id, "status": record.status},
            )
        return record

    @staticmethod
    def _normalize_argv(command: Sequence[str]) -> tuple[str, ...]:
        if isinstance(command, (str, bytes)):
            raise TypeError("verification command must be an argv sequence")
        argv = tuple(str(item) for item in (command or ()))
        if not argv:
            raise ValueError("verification command must not be empty")
        if any("\x00" in item for item in argv):
            raise ValueError("verification argv must not contain NUL")
        return argv

    @staticmethod
    def _coerce_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
