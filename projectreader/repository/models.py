"""Stable public value objects for repository discovery and inspection."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LineSpan:
    """Inclusive one-based source line span."""

    start_line: int
    end_line: int


@dataclass(frozen=True)
class CodeUnit:
    """An indexed semantic unit. This is data, not patch authority."""

    source_id: str
    unit_type: str
    name: str
    language: str
    spans: tuple[LineSpan, ...]
    char_count: int
    parent: str | None = None
    dependencies: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def start_line(self) -> int | None:
        return min((item.start_line for item in self.spans), default=None)

    @property
    def end_line(self) -> int | None:
        return max((item.end_line for item in self.spans), default=None)


@dataclass(frozen=True)
class SourceRecord:
    """Immutable identity recorded when a repository source is indexed."""

    source_id: str
    absolute_path: Path
    encoding: str
    size_bytes: int
    char_count: int
    line_count: int
    sha256: str


@dataclass(frozen=True)
class SearchResult:
    """A bounded repository search result with a safe inspection capability."""

    source_id: str
    target_ref: str
    target_type: str
    match_kind: str
    reason: str
    inspection_ref: str


@dataclass(frozen=True)
class InspectionTarget:
    """Exact target registered in the current RepositoryIndex."""

    source_id: str
    target_ref: str
    target_type: str
    indexed_kind: str
    source_digest: str
    unit_ordinal: int | None = None
    indexed_identity: str | None = None

    def canonical_key(self) -> tuple[object, ...]:
        return (
            self.source_id,
            self.target_ref,
            self.target_type,
            self.indexed_kind,
            self.source_digest,
            self.unit_ordinal,
            self.indexed_identity,
        )


@dataclass(frozen=True)
class ContextSelection:
    """Bounded, read-only source context selected from an indexed target."""

    inspection_ref: str
    source_id: str
    target_ref: str
    target_type: str
    mode: str
    content: str = ""
    segments: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class FocusTarget:
    """Current navigation focus; it does not grant patch authority."""

    inspection_ref: str
    source_id: str
    target_ref: str
    target_type: str

