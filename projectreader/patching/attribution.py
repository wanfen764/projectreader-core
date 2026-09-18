"""Factual changed-file and Python-symbol attribution from frozen bytes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from .transaction import FrozenWorkspaceChange


@dataclass(frozen=True, slots=True)
class PatchLineRange:
    """Inclusive, one-based line range derived from a frozen diff."""

    start_line: int
    end_line: int

    def __post_init__(self) -> None:
        if self.start_line <= 0 or self.end_line < self.start_line:
            raise ValueError("patch line range must be positive and non-empty")

    def as_dict(self) -> dict[str, int]:
        return {"start_line": self.start_line, "end_line": self.end_line}


@dataclass(frozen=True, slots=True)
class PatchProposalContext:
    """Navigation facts at proposal time, not an assertion of root cause."""

    focus_target_ref: str | None = None
    source_id: str | None = None
    context_mode: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "focus_target_ref": self.focus_target_ref,
            "source_id": self.source_id,
            "context_mode": self.context_mode,
        }


@dataclass(frozen=True, slots=True)
class PatchChangedScope:
    """Objective file or Python semantic scope touched by frozen bytes."""

    path: str
    change_kind: str
    scope_kind: str
    qualified_symbol: str | None
    unit_type: str | None
    changed_ranges_old: tuple[PatchLineRange, ...]
    changed_ranges_new: tuple[PatchLineRange, ...]

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("patch changed scope path must not be empty")
        if self.change_kind not in {
            "added",
            "modified",
            "deleted",
            "moved",
            "unchanged",
        }:
            raise ValueError("unsupported changed-scope change_kind")
        if self.scope_kind not in {"file", "module", "symbol"}:
            raise ValueError("unsupported changed-scope scope_kind")
        if self.scope_kind == "file":
            if self.qualified_symbol is not None or self.unit_type is not None:
                raise ValueError("file-level scope cannot claim a symbol")
        elif not self.qualified_symbol or not self.unit_type:
            raise ValueError("semantic scope requires symbol identity")

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "change_kind": self.change_kind,
            "scope_kind": self.scope_kind,
            "qualified_symbol": self.qualified_symbol,
            "unit_type": self.unit_type,
            "changed_ranges_old": [item.as_dict() for item in self.changed_ranges_old],
            "changed_ranges_new": [item.as_dict() for item in self.changed_ranges_new],
        }


@dataclass(frozen=True, slots=True)
class _PythonUnit:
    identity: tuple[str, str, str]
    start_line: int
    end_line: int
    depth: int
    source: str


class PatchAttributionAnalyzer:
    """Pure frozen-byte diff to changed-scope facts.

    This analyzer neither approves patches nor infers causal roots.  Invalid or
    undecodable Python falls back to truthful file-level attribution.
    """

    def analyze(
        self, frozen_changes: Iterable[FrozenWorkspaceChange]
    ) -> tuple[PatchChangedScope, ...]:
        scopes: list[PatchChangedScope] = []
        seen: set[str] = set()
        for change in frozen_changes:
            if change.repository_relative_path in seen:
                raise RuntimeError("frozen workspace changes contain a duplicate path")
            seen.add(change.repository_relative_path)
            scopes.extend(self._analyze_change(change))
        return tuple(sorted(scopes, key=self._scope_sort_key))

    def _analyze_change(
        self, change: FrozenWorkspaceChange
    ) -> tuple[PatchChangedScope, ...]:
        old_lines, new_lines, old_ranges, new_ranges = self._diff(change)
        if change.change_kind == "unchanged":
            return (self._file_scope(change, old_ranges, new_ranges),)
        if Path(change.repository_relative_path).suffix.lower() != ".py":
            return (self._file_scope(change, old_ranges, new_ranges),)

        old_text = self._decode_python(change.original_bytes)
        new_text = self._decode_python(change.desired_bytes)
        if old_text is None or new_text is None:
            return (self._file_scope(change, old_ranges, new_ranges),)
        old_units = self._python_units(old_text)
        new_units = self._python_units(new_text)
        if old_units is None or new_units is None:
            return (self._file_scope(change, old_ranges, new_ranges),)

        old_map = self._map_ranges(old_lines, old_ranges, old_units)
        new_map = self._map_ranges(new_lines, new_ranges, new_units)
        if old_map is None or new_map is None:
            return (self._file_scope(change, old_ranges, new_ranges),)
        identities = set(old_map) | set(new_map)
        if not identities:
            return (self._file_scope(change, old_ranges, new_ranges),)

        old_contents = {item.identity: item.source for item in old_units}
        new_contents = {item.identity: item.source for item in new_units}
        scopes: list[PatchChangedScope] = []
        for identity in identities:
            scope_kind, qualified_symbol, unit_type = identity
            in_old = identity in old_map
            in_new = identity in new_map
            if in_old and in_new:
                kind = (
                    "moved"
                    if old_contents.get(identity) == new_contents.get(identity)
                    and old_map[identity] != new_map[identity]
                    else "modified"
                )
            elif in_old:
                kind = "deleted"
            else:
                kind = "added"
            scopes.append(
                PatchChangedScope(
                    path=change.repository_relative_path,
                    change_kind=kind,
                    scope_kind=scope_kind,
                    qualified_symbol=qualified_symbol,
                    unit_type=unit_type,
                    changed_ranges_old=old_map.get(identity, ()),
                    changed_ranges_new=new_map.get(identity, ()),
                )
            )
        return tuple(scopes)

    @staticmethod
    def _diff(change: FrozenWorkspaceChange):
        old_text = (
            ""
            if change.original_bytes is None
            else change.original_bytes.decode("utf-8", errors="surrogateescape")
        )
        new_text = (
            ""
            if change.desired_bytes is None
            else change.desired_bytes.decode("utf-8", errors="surrogateescape")
        )
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        old_ranges: list[PatchLineRange] = []
        new_ranges: list[PatchLineRange] = []
        for tag, old_start, old_end, new_start, new_end in SequenceMatcher(
            a=old_lines, b=new_lines, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                continue
            if old_start < old_end:
                old_ranges.append(PatchLineRange(old_start + 1, old_end))
            if new_start < new_end:
                new_ranges.append(PatchLineRange(new_start + 1, new_end))
        return (
            old_lines,
            new_lines,
            PatchAttributionAnalyzer._merge_ranges(old_ranges),
            PatchAttributionAnalyzer._merge_ranges(new_ranges),
        )

    @staticmethod
    def _decode_python(content: bytes | None) -> str | None:
        if content is None:
            return ""
        try:
            return content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            return None

    def _python_units(self, text: str) -> tuple[_PythonUnit, ...] | None:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, TypeError):
            return None
        lines = text.splitlines(keepends=True)
        units: list[_PythonUnit] = []

        def visit_body(body, parents: tuple[str, ...], depth: int) -> None:
            for node in body:
                if not isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                ):
                    continue
                start = min(
                    [node.lineno]
                    + [item.lineno for item in getattr(node, "decorator_list", ())]
                )
                end = int(getattr(node, "end_lineno", node.lineno))
                qualified = ".".join((*parents, node.name))
                if isinstance(node, ast.ClassDef):
                    unit_type = "class"
                elif parents:
                    unit_type = "method"
                else:
                    unit_type = "function"
                units.append(
                    _PythonUnit(
                        ("symbol", qualified, unit_type),
                        start,
                        end,
                        depth,
                        "".join(lines[start - 1 : end]),
                    )
                )
                visit_body(node.body, (*parents, node.name), depth + 1)

        visit_body(tree.body, (), 1)
        module_source = "".join(
            line
            for number, line in enumerate(lines, 1)
            if not any(item.start_line <= number <= item.end_line for item in units)
        )
        units.append(
            _PythonUnit(
                ("module", "__module__", "module_context"),
                1,
                max(1, len(lines)),
                0,
                module_source,
            )
        )
        return tuple(units)

    def _map_ranges(self, lines, ranges, units):
        if not ranges:
            return {}
        mapped: dict[tuple[str, str, str], list[PatchLineRange]] = {}
        for changed_range in ranges:
            for line_number in range(changed_range.start_line, changed_range.end_line + 1):
                if line_number <= len(lines) and not lines[line_number - 1].strip():
                    continue
                containing = [
                    item
                    for item in units
                    if item.start_line <= line_number <= item.end_line
                ]
                if not containing:
                    return None
                # Prefer the deepest semantic definition. Module is the fallback.
                owner = max(containing, key=lambda item: item.depth)
                mapped.setdefault(owner.identity, []).append(
                    PatchLineRange(line_number, line_number)
                )
        # Whitespace-only changes have no reliable semantic owner.
        if not mapped:
            return None
        return {
            identity: self._merge_ranges(identity_ranges)
            for identity, identity_ranges in mapped.items()
        }

    @staticmethod
    def _merge_ranges(ranges) -> tuple[PatchLineRange, ...]:
        ordered = sorted(ranges, key=lambda item: (item.start_line, item.end_line))
        if not ordered:
            return ()
        merged = [ordered[0]]
        for item in ordered[1:]:
            previous = merged[-1]
            if item.start_line <= previous.end_line + 1:
                merged[-1] = PatchLineRange(
                    previous.start_line, max(previous.end_line, item.end_line)
                )
            else:
                merged.append(item)
        return tuple(merged)

    @staticmethod
    def _file_scope(change, old_ranges, new_ranges) -> PatchChangedScope:
        return PatchChangedScope(
            path=change.repository_relative_path,
            change_kind=change.change_kind,
            scope_kind="file",
            qualified_symbol=None,
            unit_type=None,
            changed_ranges_old=old_ranges,
            changed_ranges_new=new_ranges,
        )

    @staticmethod
    def _scope_sort_key(scope: PatchChangedScope):
        positions = [
            item.start_line
            for item in (*scope.changed_ranges_old, *scope.changed_ranges_new)
        ]
        return (
            scope.path,
            min(positions, default=0),
            {"deleted": 0, "modified": 1, "moved": 2, "added": 3, "unchanged": 4}[
                scope.change_kind
            ],
            scope.scope_kind,
            scope.qualified_symbol or "",
        )
