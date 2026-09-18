"""Bounded lexical search over an already-built RepositoryIndex."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import RepositorySearchRejected
from .inspection import InspectionRegistry, unit_target_ref
from .models import CodeUnit, SearchResult


DEFAULT_SEARCH_LIMIT = 8
MAX_SEARCH_LIMIT = 10


@dataclass(frozen=True)
class _SearchTarget:
    source_id: str
    target_ref: str
    target_type: str
    path_terms: frozenset[str]
    target_terms: frozenset[str]
    source_terms: frozenset[str]
    unit: CodeUnit | None = None


class RepositorySearch:
    """Search indexed identities and source text without filesystem traversal."""

    def __init__(self, index, registry: InspectionRegistry | None = None):
        self.index = index
        self.registry = registry or InspectionRegistry(index)
        if self.registry.index is not index:
            raise ValueError("inspection registry belongs to a different index")
        self._targets = self._build_targets()

    def search(self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT) -> list[SearchResult]:
        terms = frozenset(_terms(query))
        if not terms:
            raise RepositorySearchRejected("query_empty", "search_repository")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise RepositorySearchRejected("limit_out_of_range", "search_repository")
        ranked = []
        for target in self._targets:
            target_matches = terms & target.target_terms
            path_matches = terms & target.path_terms
            source_matches = terms & target.source_terms
            if target.target_type == "symbol" and not target_matches:
                continue
            matched = target_matches | path_matches | source_matches
            if not matched:
                continue
            kinds = []
            if target_matches:
                kinds.append("target_ref")
            if path_matches:
                kinds.append("source_path")
            if source_matches:
                kinds.append("indexed_source")
            inspection_ref = (
                self.registry.issue_file(target.source_id)
                if target.unit is None
                else self.registry.issue_unit(target.unit)
            )
            result = SearchResult(
                source_id=target.source_id,
                target_ref=target.target_ref,
                target_type=target.target_type,
                match_kind="+".join(kinds),
                reason=f"matched {len(matched)}/{len(terms)} query terms via {','.join(kinds)}",
                inspection_ref=inspection_ref,
            )
            score = len(target_matches) * 4 + len(path_matches) * 3 + len(source_matches)
            ranked.append((
                -len(matched),
                -len(target_matches),
                -len(path_matches),
                -score,
                0 if target.target_type == "symbol" else 1,
                target.source_id,
                target.target_ref,
                result,
            ))
        ranked.sort(key=lambda item: item[:-1])
        selected: list[SearchResult] = []
        symbols_by_source: dict[str, int] = {}
        for *_, result in ranked:
            if result.target_type == "symbol":
                count = symbols_by_source.get(result.source_id, 0)
                if count >= 2:
                    continue
                symbols_by_source[result.source_id] = count + 1
            selected.append(result)
            if len(selected) >= limit:
                break
        return selected

    def _build_targets(self) -> tuple[_SearchTarget, ...]:
        targets: list[_SearchTarget] = []
        source_terms: dict[str, frozenset[str]] = {}
        for source_id in sorted(self.index.source_store.records):
            content_terms = frozenset(_terms(self.index.source_store.read_full(source_id)))
            source_terms[source_id] = content_terms
            path_terms = frozenset(_terms(source_id))
            targets.append(_SearchTarget(
                source_id,
                source_id,
                "file",
                path_terms,
                path_terms,
                content_terms,
            ))
        for unit in self.index.code_units.all():
            if unit.unit_type not in {"class", "function", "method"}:
                continue
            target_ref = unit_target_ref(unit)
            targets.append(_SearchTarget(
                unit.source_id,
                target_ref,
                "symbol",
                frozenset(_terms(unit.source_id)),
                frozenset(_terms(target_ref)),
                source_terms.get(unit.source_id, frozenset()),
                unit,
            ))
        return tuple(targets)


def _terms(value: str) -> tuple[str, ...]:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or "")).replace("_", " ")
    return tuple(dict.fromkeys(token.lower() for token in re.findall(r"[A-Za-z0-9]+", text)))
