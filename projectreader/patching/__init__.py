"""Transactional patching and factual changed-scope attribution."""

from .attribution import (
    PatchAttributionAnalyzer,
    PatchChangedScope,
    PatchLineRange,
    PatchProposalContext,
)
from .manager import (
    PatchApplyFailed,
    PatchManager,
    PatchRecord,
    PatchRollbackFailed,
    PatchStore,
    PatchVerification,
)
from .transaction import (
    FrozenWorkspaceChange,
    WorkspaceTransaction,
    WorkspaceTransactionApplyError,
    WorkspaceTransactionReport,
    WorkspaceTransactionRestoreError,
    WorkspaceTransactionState,
)

__all__ = [
    "FrozenWorkspaceChange",
    "PatchApplyFailed",
    "PatchAttributionAnalyzer",
    "PatchChangedScope",
    "PatchLineRange",
    "PatchManager",
    "PatchProposalContext",
    "PatchRecord",
    "PatchRollbackFailed",
    "PatchStore",
    "PatchVerification",
    "WorkspaceTransaction",
    "WorkspaceTransactionApplyError",
    "WorkspaceTransactionReport",
    "WorkspaceTransactionRestoreError",
    "WorkspaceTransactionState",
]
