"""Internal repository state stores."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from pathlib import Path

from .models import CodeUnit, SourceRecord


class SourceStore:
    """Repository-scoped source authority created only by RepositoryIndexer."""

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        self._records: dict[str, SourceRecord] = {}

    @property
    def records(self) -> dict[str, SourceRecord]:
        return dict(self._records)

    def register(self, file_path: Path, content: str, encoding: str) -> str:
        path = file_path.resolve()
        try:
            relative = path.relative_to(self.project_root)
        except ValueError as exc:
            raise ValueError("source must be inside the registered repository") from exc
        source_id = relative.as_posix()
        self._records[source_id] = SourceRecord(
            source_id=source_id,
            absolute_path=path,
            encoding=encoding,
            size_bytes=path.stat().st_size,
            char_count=len(content),
            line_count=len(content.splitlines()),
            sha256=_file_digest(path),
        )
        return source_id

    def get_record(self, source_id: str) -> SourceRecord | None:
        return self._records.get(source_id)

    def is_current(self, source_id: str) -> bool:
        record = self._records.get(source_id)
        return bool(
            record
            and record.absolute_path.is_file()
            and _file_digest(record.absolute_path) == record.sha256
        )

    def read_full(self, source_id: str) -> str:
        record = self._records.get(source_id)
        if record is None:
            raise KeyError(f"unknown source_id: {source_id}")
        if not self.is_current(source_id):
            raise RuntimeError(f"source changed since indexing: {source_id}")
        return record.absolute_path.read_text(encoding=record.encoding)

    def read_lines(self, source_id: str, start_line: int, end_line: int) -> str:
        if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1:
            raise ValueError("start_line must be a positive integer")
        if isinstance(end_line, bool) or not isinstance(end_line, int) or end_line < start_line:
            raise ValueError("end_line must be an integer at least start_line")
        lines = self.read_full(source_id).splitlines(keepends=True)
        if start_line > len(lines):
            raise ValueError("start_line is outside the source")
        return "".join(lines[start_line - 1 : min(end_line, len(lines))])

    def summary(self) -> dict[str, int]:
        records = tuple(self._records.values())
        return {
            "files": len(records),
            "bytes": sum(item.size_bytes for item in records),
            "characters": sum(item.char_count for item in records),
            "lines": sum(item.line_count for item in records),
        }


class CodeUnitStore:
    def __init__(self):
        self._units: list[CodeUnit] = []
        self._by_source: dict[str, list[CodeUnit]] = defaultdict(list)
        self._by_name: dict[str, list[CodeUnit]] = defaultdict(list)
        self._by_qualified_name: dict[str, list[CodeUnit]] = defaultdict(list)

    def register(self, unit: CodeUnit) -> None:
        self._units.append(unit)
        self._by_source[unit.source_id].append(unit)
        self._by_name[unit.name].append(unit)
        qualified = unit.metadata.get("qualified_name")
        if qualified:
            self._by_qualified_name[str(qualified)].append(unit)
        elif unit.parent and unit.unit_type in {"method", "method_part"}:
            self._by_qualified_name[f"{unit.parent}.{unit.name}"].append(unit)
        elif unit.parent is None and unit.unit_type in {"function", "function_part"}:
            self._by_qualified_name[unit.name].append(unit)

    def extend(self, units) -> None:
        for unit in units:
            self.register(unit)

    def all(self) -> list[CodeUnit]:
        return list(self._units)

    def by_source(self, source_id: str) -> list[CodeUnit]:
        return list(self._by_source.get(source_id, ()))

    def by_name(self, name: str) -> list[CodeUnit]:
        return list(self._by_name.get(name, ()))

    def by_qualified_name(self, qualified_name: str) -> list[CodeUnit]:
        return list(self._by_qualified_name.get(qualified_name, ()))

    def summary(self) -> dict[str, object]:
        by_type: dict[str, int] = defaultdict(int)
        for unit in self._units:
            by_type[unit.unit_type] += 1
        return {
            "total": len(self._units),
            "sources": len(self._by_source),
            "by_type": dict(sorted(by_type.items())),
        }


class TextChunkStore:
    def __init__(self):
        self._by_source: dict[str, list[dict[str, object]]] = defaultdict(list)

    def register(self, source_id: str, chunk: dict[str, object]) -> None:
        self._by_source[source_id].append({**chunk, "source_id": source_id})

    def by_source(self, source_id: str) -> list[dict[str, object]]:
        return [dict(item) for item in self._by_source.get(source_id, ())]

    def summary(self) -> dict[str, int]:
        return {
            "total": sum(len(items) for items in self._by_source.values()),
            "sources": len(self._by_source),
        }


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
