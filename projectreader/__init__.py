"""ProjectReader Core public API."""

from .actions.rejections import ProjectReaderRejected
from .patching import PatchRecord, PatchVerification
from .repository import ContextSelection, FocusTarget, InspectionTarget, SearchResult
from .runtime import ProjectReader, ProjectReaderStatus

__version__ = "0.1.0a1"

__all__ = [
    "ContextSelection",
    "FocusTarget",
    "InspectionTarget",
    "PatchRecord",
    "PatchVerification",
    "ProjectReader",
    "ProjectReaderRejected",
    "ProjectReaderStatus",
    "SearchResult",
    "__version__",
]
