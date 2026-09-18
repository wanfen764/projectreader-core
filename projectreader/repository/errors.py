"""Typed repository rejections safe to expose across public boundaries."""

from __future__ import annotations

from projectreader.actions.rejections import ProjectReaderRejected


class RepositoryRejected(ProjectReaderRejected):
    """Base class for closed, side-effect-free repository rejections."""

    def structured(self) -> dict[str, object]:
        """Compatibility spelling used by direct Python callers."""
        return self.as_error()


class RepositorySearchRejected(RepositoryRejected):
    ERROR_TYPE = "repository_search_precondition"
    REASON_ACTIONS = {
        "query_empty": frozenset({"search_repository"}),
        "limit_out_of_range": frozenset({"search_repository"}),
    }
    REASON_MESSAGES = {
        "query_empty": "The search query must contain a searchable term",
        "limit_out_of_range": "The requested search limit is outside the supported range",
    }


class RepositoryInspectionRejected(RepositoryRejected):
    ERROR_TYPE = "repository_inspection_precondition"
    _ACTIONS = frozenset({"inspect_repository_target", "focus_repository_target"})
    REASON_ACTIONS = {
        "target_not_indexed": _ACTIONS,
        "target_ambiguous": _ACTIONS,
        "target_not_symbol": _ACTIONS,
        "malformed_ref": _ACTIONS,
        "inspection_ref_wrong_session": _ACTIONS,
        "inspection_ref_unknown": _ACTIONS,
        "inspection_ref_stale": _ACTIONS,
        "segment_index_invalid": _ACTIONS,
        "segment_index_out_of_range": _ACTIONS,
    }
    REASON_MESSAGES = {
        "target_not_indexed": "The target is not registered in this repository index",
        "target_ambiguous": "The target does not resolve to one exact indexed symbol",
        "target_not_symbol": "The inspection target is not an indexed symbol",
        "malformed_ref": "The inspection reference is malformed",
        "inspection_ref_wrong_session": "The inspection reference belongs to another session",
        "inspection_ref_unknown": "The inspection reference is not registered",
        "inspection_ref_stale": "The indexed source changed after the inspection reference was issued",
        "segment_index_invalid": "The segment index must be a positive integer",
        "segment_index_out_of_range": "The segment index is outside the target context",
    }
