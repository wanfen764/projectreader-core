"""Repository-scoped opaque inspection capabilities and context loading."""

from __future__ import annotations

import hashlib
import json
import re
import uuid

from .errors import RepositoryInspectionRejected
from .models import CodeUnit, ContextSelection, InspectionTarget


_REF_PATTERN = re.compile(r"^inspection-v1:([0-9a-f]{32}):([0-9]{6})$")


class InspectionRegistry:
    """Per-session authority for targets already present in RepositoryIndex."""

    def __init__(self, index, *, namespace: str | None = None):
        self.index = index
        self._namespace = uuid.uuid4().hex if namespace is None else namespace
        if not isinstance(self._namespace, str) or not re.fullmatch(r"[0-9a-f]{32}", self._namespace):
            raise ValueError("namespace must be 32 lowercase hexadecimal characters")
        self._by_ref: dict[str, InspectionTarget] = {}
        self._by_target: dict[tuple[object, ...], str] = {}

    def issue_file(self, source_id: str) -> str:
        record = self.index.source_store.get_record(source_id)
        if record is None:
            raise RepositoryInspectionRejected("target_not_indexed", "inspect_repository_target")
        return self._issue(InspectionTarget(
            source_id=source_id,
            target_ref=source_id,
            target_type="file",
            indexed_kind="file",
            source_digest=record.sha256,
        ))

    def issue_symbol(self, source_id: str, target_ref: str) -> str:
        matches = [
            (ordinal, unit)
            for ordinal, unit in enumerate(self.index.code_units.by_source(source_id))
            if unit_target_ref(unit) == target_ref
            and unit.unit_type in {"class", "function", "method"}
        ]
        if len(matches) != 1:
            reason = "target_not_indexed" if not matches else "target_ambiguous"
            raise RepositoryInspectionRejected(reason, "inspect_repository_target")
        ordinal, unit = matches[0]
        return self.issue_unit(unit, ordinal=ordinal)

    def issue_unit(self, unit: CodeUnit, *, ordinal: int | None = None) -> str:
        record = self.index.source_store.get_record(unit.source_id)
        units = self.index.code_units.by_source(unit.source_id)
        if record is None:
            raise RepositoryInspectionRejected("target_not_indexed", "inspect_repository_target")
        if ordinal is None:
            ordinal = next((position for position, item in enumerate(units) if item is unit), None)
        if ordinal is None or ordinal < 0 or ordinal >= len(units) or units[ordinal] is not unit:
            raise RepositoryInspectionRejected("target_not_indexed", "inspect_repository_target")
        return self._issue(InspectionTarget(
            source_id=unit.source_id,
            target_ref=unit_target_ref(unit),
            target_type="symbol",
            indexed_kind="code_unit",
            source_digest=record.sha256,
            unit_ordinal=ordinal,
            indexed_identity=_unit_digest(unit),
        ))

    def resolve(self, inspection_ref: str) -> InspectionTarget:
        if not isinstance(inspection_ref, str):
            raise RepositoryInspectionRejected("malformed_ref", "inspect_repository_target")
        match = _REF_PATTERN.fullmatch(inspection_ref)
        if match is None:
            raise RepositoryInspectionRejected("malformed_ref", "inspect_repository_target")
        if match.group(1) != self._namespace:
            raise RepositoryInspectionRejected("inspection_ref_wrong_session", "inspect_repository_target")
        target = self._by_ref.get(inspection_ref)
        if target is None:
            raise RepositoryInspectionRejected("inspection_ref_unknown", "inspect_repository_target")
        record = self.index.source_store.get_record(target.source_id)
        if record is None or record.sha256 != target.source_digest or not self.index.source_store.is_current(target.source_id):
            raise RepositoryInspectionRejected("inspection_ref_stale", "inspect_repository_target")
        if target.indexed_kind == "code_unit":
            self.unit(target)
        return target

    def unit(self, target: InspectionTarget) -> CodeUnit:
        if target.indexed_kind != "code_unit" or target.unit_ordinal is None:
            raise RepositoryInspectionRejected("target_not_symbol", "inspect_repository_target")
        units = self.index.code_units.by_source(target.source_id)
        if target.unit_ordinal < 0 or target.unit_ordinal >= len(units):
            raise RepositoryInspectionRejected("inspection_ref_stale", "inspect_repository_target")
        unit = units[target.unit_ordinal]
        if _unit_digest(unit) != target.indexed_identity:
            raise RepositoryInspectionRejected("inspection_ref_stale", "inspect_repository_target")
        return unit

    def refs(self) -> tuple[str, ...]:
        return tuple(self._by_ref)

    def _issue(self, target: InspectionTarget) -> str:
        key = target.canonical_key()
        if key in self._by_target:
            return self._by_target[key]
        sequence = len(self._by_ref) + 1
        if sequence > 999999:
            raise RuntimeError("inspection registry capacity exhausted")
        inspection_ref = f"inspection-v1:{self._namespace}:{sequence:06d}"
        self._by_ref[inspection_ref] = target
        self._by_target[key] = inspection_ref
        return inspection_ref


class RepositoryInspector:
    """Load bounded source context using only repository-issued references."""

    def __init__(self, index, registry: InspectionRegistry, *, max_inline_chars: int = 12000):
        if registry.index is not index:
            raise ValueError("inspection registry belongs to a different index")
        self.index = index
        self.registry = registry
        self.max_inline_chars = max_inline_chars

    def target(self, inspection_ref: str) -> InspectionTarget:
        return self.registry.resolve(inspection_ref)

    def inspect(self, inspection_ref: str, segment_index: int | None = None) -> ContextSelection:
        target = self.registry.resolve(inspection_ref)
        if segment_index is not None:
            if isinstance(segment_index, bool) or not isinstance(segment_index, int) or segment_index < 1:
                raise RepositoryInspectionRejected("segment_index_invalid", "inspect_repository_target")
            return self._segment(inspection_ref, target, segment_index)
        if target.indexed_kind == "file":
            return self._file(inspection_ref, target)
        return self._symbol(inspection_ref, target)

    def inspect_target(self, source_id: str, target_ref: str | None = None, segment_index: int | None = None):
        inspection_ref = (
            self.registry.issue_file(source_id)
            if target_ref is None or target_ref == source_id
            else self.registry.issue_symbol(source_id, target_ref)
        )
        return self.inspect(inspection_ref, segment_index)

    def _file(self, inspection_ref: str, target: InspectionTarget) -> ContextSelection:
        record = self.index.source_store.get_record(target.source_id)
        if record is None:
            raise RepositoryInspectionRejected("target_not_indexed", "inspect_repository_target")
        units = self.index.code_units.by_source(target.source_id)
        if units:
            logical = [item for item in units if item.unit_type in {"module_context", "class", "function", "method"}]
            segments = tuple({
                "unit_type": item.unit_type,
                "target_ref": unit_target_ref(item),
                "start_line": item.start_line,
                "end_line": item.end_line,
                "char_count": item.char_count,
                "split": bool(item.metadata.get("split")),
            } for item in sorted(logical, key=lambda item: (item.start_line or 10**12, item.unit_type, item.name)))
            return ContextSelection(
                inspection_ref,
                target.source_id,
                target.target_ref,
                target.target_type,
                "python_outline",
                segments=segments,
                metadata={"line_count": record.line_count, "char_count": record.char_count},
            )
        chunks = self.index.text_chunks.by_source(target.source_id)
        if record.char_count <= self.max_inline_chars:
            return ContextSelection(
                inspection_ref,
                target.source_id,
                target.target_ref,
                target.target_type,
                "text_full",
                content=self.index.source_store.read_full(target.source_id),
                metadata={"line_count": record.line_count, "char_count": record.char_count},
            )
        return ContextSelection(
            inspection_ref,
            target.source_id,
            target.target_ref,
            target.target_type,
            "text_chunk_index",
            segments=tuple({
                "segment_index": item["chunk_index"],
                "start_line": item["start_line"],
                "end_line": item["end_line"],
                "char_count": len(str(item["content"])),
            } for item in chunks),
            metadata={"line_count": record.line_count, "char_count": record.char_count, "segment_count": len(chunks)},
        )

    def _symbol(self, inspection_ref: str, target: InspectionTarget) -> ContextSelection:
        unit = self.registry.unit(target)
        if unit.metadata.get("split"):
            parts = self._parts(unit)
            return ContextSelection(
                inspection_ref,
                target.source_id,
                target.target_ref,
                target.target_type,
                "split_callable_index",
                content=self._read_spans(target.source_id, unit.spans),
                segments=tuple({
                    "segment_index": position,
                    "start_line": item.start_line,
                    "end_line": item.end_line,
                    "char_count": item.char_count,
                } for position, item in enumerate(parts, 1)),
                metadata={"segment_count": len(parts), "unit_type": unit.unit_type},
            )
        return ContextSelection(
            inspection_ref,
            target.source_id,
            target.target_ref,
            target.target_type,
            "symbol_source",
            content=self._read_spans(target.source_id, unit.spans),
            metadata={
                "unit_type": unit.unit_type,
                "start_line": unit.start_line,
                "end_line": unit.end_line,
                "char_count": unit.char_count,
            },
        )

    def _segment(self, inspection_ref: str, target: InspectionTarget, segment_index: int):
        if target.indexed_kind == "file":
            chunks = self.index.text_chunks.by_source(target.source_id)
            if not chunks or segment_index > len(chunks):
                raise RepositoryInspectionRejected("segment_index_out_of_range", "inspect_repository_target")
            chunk = chunks[segment_index - 1]
            return ContextSelection(
                inspection_ref,
                target.source_id,
                target.target_ref,
                target.target_type,
                "text_chunk",
                content=str(chunk["content"]),
                metadata={"segment_index": segment_index, "segment_count": len(chunks)},
            )
        unit = self.registry.unit(target)
        parts = self._parts(unit)
        if not parts or segment_index > len(parts):
            raise RepositoryInspectionRejected("segment_index_out_of_range", "inspect_repository_target")
        part = parts[segment_index - 1]
        return ContextSelection(
            inspection_ref,
            target.source_id,
            target.target_ref,
            target.target_type,
            "callable_part",
            content=self._read_spans(target.source_id, part.spans),
            metadata={"segment_index": segment_index, "segment_count": len(parts)},
        )

    def _parts(self, container: CodeUnit) -> list[CodeUnit]:
        expected = f"{container.unit_type}_part"
        qualified = str(container.metadata.get("qualified_name", container.name))
        return sorted(
            (item for item in self.index.code_units.by_source(container.source_id) if item.unit_type == expected and item.parent == qualified),
            key=lambda item: (item.metadata.get("part_index", 10**12), item.start_line or 10**12),
        )

    def _read_spans(self, source_id: str, spans) -> str:
        return "".join(self.index.source_store.read_lines(source_id, item.start_line, item.end_line) for item in spans)


def unit_target_ref(unit: CodeUnit) -> str:
    if qualified := unit.metadata.get("qualified_name"):
        return str(qualified)
    if unit.parent and unit.unit_type in {"method", "method_part"}:
        return f"{unit.parent}.{unit.name}"
    if unit.unit_type == "module_context":
        return "__module__"
    return unit.name


def _unit_digest(unit: CodeUnit) -> str:
    value = {
        "source_id": unit.source_id,
        "unit_type": unit.unit_type,
        "name": unit.name,
        "parent": unit.parent,
        "spans": [[item.start_line, item.end_line] for item in unit.spans],
        "qualified_name": unit.metadata.get("qualified_name"),
        "split": bool(unit.metadata.get("split")),
    }
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
