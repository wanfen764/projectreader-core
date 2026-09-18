"""Objective, repository-local import relations."""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(frozen=True)
class FileRelation:
    source_id: str
    target_id: str
    kind: str
    detail: str = ""
    metadata: dict[str, object] = field(default_factory=dict, compare=False)


class RepositoryRelations:
    def __init__(self):
        self._outgoing: dict[str, list[FileRelation]] = defaultdict(list)
        self._incoming: dict[str, list[FileRelation]] = defaultdict(list)
        self._seen: set[tuple[str, str, str, str]] = set()

    def register(self, source_id: str, target_id: str, kind: str, detail: str = "", metadata=None):
        if not source_id or not target_id:
            raise ValueError("source_id and target_id are required")
        key = (source_id, target_id, kind, detail)
        if source_id == target_id or key in self._seen:
            return None
        self._seen.add(key)
        relation = FileRelation(source_id, target_id, kind, detail, dict(metadata or {}))
        self._outgoing[source_id].append(relation)
        self._incoming[target_id].append(relation)
        return relation

    def related(self, source_id: str) -> list[FileRelation]:
        result = [
            FileRelation(source_id, item.target_id, "imports", item.detail, dict(item.metadata))
            for item in self._outgoing.get(source_id, ())
        ]
        result.extend(
            FileRelation(source_id, item.source_id, "imported_by", item.detail, dict(item.metadata))
            for item in self._incoming.get(source_id, ())
        )
        return sorted(result, key=lambda item: (item.target_id, item.kind, item.detail))

    def summary(self) -> dict[str, int]:
        return {
            "edges": sum(len(items) for items in self._outgoing.values()),
            "sources_with_outgoing": len(self._outgoing),
            "targets_with_incoming": len(self._incoming),
        }


class PythonImportRelationBuilder:
    def build(self, source_store) -> RepositoryRelations:
        relations = RepositoryRelations()
        records = source_store.records
        module_map = {
            module: source_id
            for source_id in records
            if (module := _module_name(source_id)) is not None
        }
        for source_id in sorted(records):
            if not source_id.lower().endswith(".py"):
                continue
            try:
                tree = ast.parse(source_store.read_full(source_id))
            except (SyntaxError, UnicodeError, RuntimeError):
                continue
            package = _package_name(source_id)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        target = _resolve_module(alias.name, module_map)
                        if target:
                            relations.register(source_id, target, "import", alias.name, {"lineno": node.lineno})
                elif isinstance(node, ast.ImportFrom):
                    module = _resolve_from(package, node.module, node.level)
                    targets: list[tuple[str, str]] = []
                    for alias in node.names:
                        if alias.name == "*":
                            continue
                        detail = f"{module}.{alias.name}" if module else alias.name
                        if target := _resolve_module(detail, module_map):
                            targets.append((target, detail))
                    if target := _resolve_module(module, module_map):
                        targets.append((target, module))
                    seen = set()
                    for target, detail in targets:
                        if target in seen:
                            continue
                        seen.add(target)
                        relations.register(source_id, target, "from_import", detail, {"lineno": node.lineno})
        return relations


def _module_name(source_id: str) -> str | None:
    path = PurePosixPath(source_id.replace("\\", "/"))
    if path.suffix.lower() != ".py":
        return None
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_name(source_id: str) -> str:
    module = _module_name(source_id) or ""
    path = PurePosixPath(source_id.replace("\\", "/"))
    if path.name == "__init__.py":
        return module
    return module.rsplit(".", 1)[0] if "." in module else ""


def _resolve_module(module: str | None, module_map: dict[str, str]) -> str | None:
    if not module:
        return None
    parts = module.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in module_map:
            return module_map[candidate]
        parts.pop()
    return None


def _resolve_from(package: str, module: str | None, level: int) -> str:
    if level <= 0:
        return module or ""
    parts = [part for part in package.split(".") if part]
    climb = max(level - 1, 0)
    if climb:
        parts = parts[:-climb] if climb <= len(parts) else []
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)

