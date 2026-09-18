"""Deterministic text and Python semantic indexing."""

from __future__ import annotations

import ast
from pathlib import Path

from .models import CodeUnit, LineSpan


class TextChunker:
    def __init__(self, max_chars: int = 12000):
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        self.max_chars = max_chars

    def iter_chunks(self, path: Path, content: str):
        if not content:
            return
        lines = content.splitlines(keepends=True)
        bucket: list[str] = []
        bucket_chars = 0
        bucket_start = 1
        chunk_index = 0

        def emit(end_line: int):
            nonlocal bucket, bucket_chars, chunk_index
            if not bucket:
                return None
            chunk_index += 1
            result = {
                "file": str(path),
                "chunk_index": chunk_index,
                "start_line": bucket_start,
                "end_line": end_line,
                "content": "".join(bucket),
            }
            bucket = []
            bucket_chars = 0
            return result

        for line_number, line in enumerate(lines, start=1):
            if len(line) > self.max_chars:
                item = emit(line_number - 1)
                if item:
                    yield item
                position = 0
                while position < len(line):
                    chunk_index += 1
                    yield {
                        "file": str(path),
                        "chunk_index": chunk_index,
                        "start_line": line_number,
                        "end_line": line_number,
                        "content": line[position : position + self.max_chars],
                    }
                    position += self.max_chars
                bucket_start = line_number + 1
                continue
            if bucket and bucket_chars + len(line) > self.max_chars:
                item = emit(line_number - 1)
                if item:
                    yield item
                bucket_start = line_number
            bucket.append(line)
            bucket_chars += len(line)
        item = emit(len(lines))
        if item:
            yield item


class PythonSemanticIndexer:
    """Index files into exact classes/functions/methods and bounded parts."""

    def __init__(self, max_unit_chars: int = 12000):
        self.max_unit_chars = max_unit_chars

    def chunk(self, path: Path, content: str, source_id: str) -> dict[str, object]:
        try:
            tree = ast.parse(content)
        except SyntaxError as exc:
            return {"success": False, "chunks": [], "error": str(exc)}
        lines = content.splitlines(keepends=True)
        units: list[CodeUnit] = []
        module_nodes: list[ast.AST] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.extend(self._callable_units(node, source_id, lines, "function", None))
            elif isinstance(node, ast.ClassDef):
                units.extend(self._class_units(node, source_id, lines, None))
            else:
                module_nodes.append(node)
        if module_nodes:
            spans = tuple(self._span(node) for node in module_nodes)
            units.insert(0, self._unit(source_id, "module_context", "__module__", spans, lines))
        units.extend(self._raw_gaps(source_id, lines, units))
        return {"success": True, "chunks": units, "error": None}

    def _class_units(self, node: ast.ClassDef, source_id: str, lines: list[str], parent: str | None):
        qualified = f"{parent}.{node.name}" if parent else node.name
        child_definitions = tuple(
            child for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        )
        spans: list[LineSpan] = []
        start = self._start(node)
        first_child = min((self._start(child) for child in child_definitions), default=node.end_lineno + 1)
        if first_child > start:
            spans.append(LineSpan(start, first_child - 1))
        for child in node.body:
            if child not in child_definitions:
                spans.append(self._span(child))
        units = [self._unit(
            source_id,
            "class",
            node.name,
            tuple(spans),
            lines,
            parent=parent,
            metadata={
                "qualified_name": qualified,
                "methods": [item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))],
                "nested_classes": [item.name for item in node.body if isinstance(item, ast.ClassDef)],
                "bases": [ast.unparse(item) for item in node.bases],
            },
        )]
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                units.extend(self._callable_units(child, source_id, lines, "method", qualified))
            elif isinstance(child, ast.ClassDef):
                units.extend(self._class_units(child, source_id, lines, qualified))
        return units

    def _callable_units(self, node, source_id: str, lines: list[str], unit_type: str, parent: str | None):
        start, end = self._start(node), node.end_lineno
        size = len("".join(lines[start - 1 : end]))
        qualified = f"{parent}.{node.name}" if parent else node.name
        if size <= self.max_unit_chars:
            return [self._unit(
                source_id,
                unit_type,
                node.name,
                (LineSpan(start, end),),
                lines,
                parent=parent,
                metadata={"qualified_name": qualified},
            )]

        header_end = min((item.lineno - 1 for item in node.body), default=start)
        container = self._unit(
            source_id,
            unit_type,
            node.name,
            (LineSpan(start, max(start, header_end)),),
            lines,
            parent=parent,
            metadata={
                "qualified_name": qualified,
                "split": True,
                "original_start_line": start,
                "original_end_line": end,
                "original_char_count": size,
            },
        )
        parts: list[CodeUnit] = []
        part_start = max(start, header_end + 1)
        cursor = part_start
        part_index = 0
        while cursor <= end:
            current = cursor
            count = 0
            while current <= end:
                line_size = len(lines[current - 1])
                if current > cursor and count + line_size > self.max_unit_chars:
                    break
                count += line_size
                current += 1
            part_index += 1
            part_end = current - 1
            parts.append(self._unit(
                source_id,
                f"{unit_type}_part",
                node.name,
                (LineSpan(cursor, part_end),),
                lines,
                parent=qualified,
                metadata={"part_index": part_index},
            ))
            cursor = current
        container.metadata["part_count"] = len(parts)
        return [container, *parts]

    def _raw_gaps(self, source_id: str, lines: list[str], units: list[CodeUnit]):
        covered = [False] * len(lines)
        for unit in units:
            for span in unit.spans:
                for offset in range(max(1, span.start_line) - 1, min(len(lines), span.end_line)):
                    covered[offset] = True
        results = []
        position = 0
        sequence = 0
        while position < len(lines):
            if covered[position]:
                position += 1
                continue
            start = position
            while position < len(lines) and not covered[position]:
                position += 1
            if not "".join(lines[start:position]).strip():
                continue
            sequence += 1
            results.append(self._unit(
                source_id,
                "raw_gap",
                f"__raw_gap_{sequence}__",
                (LineSpan(start + 1, position),),
                lines,
            ))
        return results

    @staticmethod
    def _start(node: ast.AST) -> int:
        decorators = getattr(node, "decorator_list", ())
        return min([node.lineno, *(item.lineno for item in decorators)])

    def _span(self, node: ast.AST) -> LineSpan:
        return LineSpan(self._start(node), node.end_lineno)

    @staticmethod
    def _unit(source_id, unit_type, name, spans, lines, *, parent=None, metadata=None):
        merged = _merge_spans(spans)
        return CodeUnit(
            source_id=source_id,
            unit_type=unit_type,
            name=name,
            language="python",
            spans=merged,
            char_count=sum(len("".join(lines[item.start_line - 1 : item.end_line])) for item in merged),
            parent=parent,
            metadata=dict(metadata or {}),
        )


def _merge_spans(spans) -> tuple[LineSpan, ...]:
    ordered = sorted(spans, key=lambda item: (item.start_line, item.end_line))
    merged: list[LineSpan] = []
    for span in ordered:
        if merged and span.start_line <= merged[-1].end_line + 1:
            merged[-1] = LineSpan(merged[-1].start_line, max(merged[-1].end_line, span.end_line))
        else:
            merged.append(span)
    return tuple(merged)
