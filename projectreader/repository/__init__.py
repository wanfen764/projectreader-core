"""Public repository indexing, search, and inspection API."""

from .errors import (
    RepositoryInspectionRejected,
    RepositoryRejected,
    RepositorySearchRejected,
)
from .index import RepositoryIndex, RepositoryIndexer
from .inspection import InspectionRegistry, RepositoryInspector
from .models import ContextSelection, FocusTarget, InspectionTarget, SearchResult
from .search import DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT, RepositorySearch

__all__ = [
    "ContextSelection",
    "DEFAULT_SEARCH_LIMIT",
    "FocusTarget",
    "InspectionRegistry",
    "InspectionTarget",
    "MAX_SEARCH_LIMIT",
    "RepositoryIndex",
    "RepositoryIndexer",
    "RepositoryInspectionRejected",
    "RepositoryInspector",
    "RepositoryRejected",
    "RepositorySearch",
    "RepositorySearchRejected",
    "SearchResult",
]
