"""Standalone repository indexing entry point."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._scanner import ProjectScanner, TextFileReader
from ._semantic import PythonSemanticIndexer, TextChunker
from ._stores import CodeUnitStore, SourceStore, TextChunkStore
from .relations import PythonImportRelationBuilder, RepositoryRelations


@dataclass(frozen=True)
class RepositoryIndex:
    root: Path
    files: tuple[Path, ...]
    source_store: SourceStore
    code_units: CodeUnitStore
    text_chunks: TextChunkStore
    relations: RepositoryRelations
    stats: dict[str, object]
    file_status: dict[str, dict[str, object]]

    @property
    def project_path(self) -> str:
        return str(self.root)


class RepositoryIndexer:
    """Build repository-owned source, semantic, text, and import indexes."""

    def __init__(self, repository: str | Path, max_chars: int = 12000):
        self.repository = Path(repository)
        self.max_chars = max_chars

    def build(self) -> RepositoryIndex:
        files = ProjectScanner(self.repository).scan()
        root = self.repository.resolve()
        source_store = SourceStore(root)
        code_units = CodeUnitStore()
        text_chunks = TextChunkStore()
        reader = TextFileReader()
        text_chunker = TextChunker(self.max_chars)
        semantic = PythonSemanticIndexer(self.max_chars)
        status: dict[str, dict[str, object]] = {}
        stats: dict[str, object] = {
            "files": len(files),
            "read": 0,
            "unsupported": 0,
            "failed": 0,
            "python_files": 0,
            "python_parse_failed": 0,
            "python_semantic_chunks": 0,
            "text_chunks": 0,
            "empty_files": 0,
        }
        for path in files:
            source_id = path.relative_to(root).as_posix()
            try:
                decoded = reader.read(path)
            except OSError as exc:
                status[source_id] = {"status": "failed", "error": type(exc).__name__}
                stats["failed"] += 1
                continue
            if decoded is None:
                status[source_id] = {"status": "unsupported", "error": "binary_or_unknown_encoding"}
                stats["unsupported"] += 1
                continue
            content, encoding = decoded
            source_id = source_store.register(path, content, encoding)
            status[source_id] = {"status": "read", "error": None}
            stats["read"] += 1
            if not content:
                stats["empty_files"] += 1
                continue
            if path.suffix.lower() == ".py":
                stats["python_files"] += 1
                result = semantic.chunk(path, content, source_id)
                if result["success"]:
                    units = result["chunks"]
                    code_units.extend(units)
                    stats["python_semantic_chunks"] += len(units)
                    continue
                stats["python_parse_failed"] += 1
            for chunk in text_chunker.iter_chunks(path, content):
                text_chunks.register(source_id, chunk)
                stats["text_chunks"] += 1
        relations = PythonImportRelationBuilder().build(source_store)
        return RepositoryIndex(
            root=root,
            files=tuple(files),
            source_store=source_store,
            code_units=code_units,
            text_chunks=text_chunks,
            relations=relations,
            stats=stats,
            file_status=status,
        )
