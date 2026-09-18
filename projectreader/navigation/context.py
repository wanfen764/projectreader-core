"""Minimal, Candidate-free navigation and bounded context state."""

from __future__ import annotations

from dataclasses import dataclass

from projectreader.repository.models import ContextSelection, FocusTarget


class ContextCapacityExceeded(RuntimeError):
    """A context selection would exceed the configured deterministic budget."""


@dataclass(frozen=True)
class ContextCapacityProjection:
    inspection_ref: str
    current_chars: int
    replaced_chars: int
    incoming_chars: int
    projected_chars: int
    max_chars: int


class ContextWorkspace:
    """Bounded selections loaded during one repository session."""

    def __init__(self, max_chars: int = 48000):
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        self.max_chars = max_chars
        self._items: dict[str, tuple[ContextSelection, int, int]] = {}
        self._sequence = 0

    def preflight(self, selection: ContextSelection) -> ContextCapacityProjection:
        incoming = estimate_context_chars(selection)
        prior = self._items.get(selection.inspection_ref)
        replaced = 0 if prior is None else prior[1]
        projection = ContextCapacityProjection(
            inspection_ref=selection.inspection_ref,
            current_chars=self.current_chars,
            replaced_chars=replaced,
            incoming_chars=incoming,
            projected_chars=self.current_chars - replaced + incoming,
            max_chars=self.max_chars,
        )
        if projection.projected_chars > projection.max_chars:
            raise ContextCapacityExceeded(
                "context capacity exceeded: "
                f"projected={projection.projected_chars}, max={projection.max_chars}"
            )
        return projection

    def put(self, selection: ContextSelection) -> ContextSelection:
        projection = self.preflight(selection)
        self._sequence += 1
        self._items[selection.inspection_ref] = (selection, projection.incoming_chars, self._sequence)
        return selection

    def get(self, inspection_ref: str) -> ContextSelection | None:
        item = self._items.get(inspection_ref)
        return None if item is None else item[0]

    def evict(self, inspection_ref: str) -> ContextSelection | None:
        item = self._items.pop(inspection_ref, None)
        return None if item is None else item[0]

    def clear(self) -> tuple[ContextSelection, ...]:
        items = self.selections()
        self._items.clear()
        return items

    def selections(self) -> tuple[ContextSelection, ...]:
        return tuple(item[0] for item in sorted(self._items.values(), key=lambda entry: entry[2]))

    @property
    def current_chars(self) -> int:
        return sum(item[1] for item in self._items.values())

    def summary(self) -> dict[str, object]:
        return {
            "entries": len(self._items),
            "current_chars": self.current_chars,
            "max_chars": self.max_chars,
            "targets": [item.target_ref for item in self.selections()],
        }


class RepositoryNavigator:
    """Select one active context without Candidate lifecycle semantics."""

    def __init__(self, inspector, *, max_chars: int = 48000):
        self.inspector = inspector
        self.workspace = ContextWorkspace(max_chars=max_chars)
        self.active: FocusTarget | None = None
        self.context: ContextSelection | None = None

    def focus(self, inspection_ref: str, segment_index: int | None = None) -> ContextSelection:
        selection = self.inspector.inspect(inspection_ref, segment_index)
        self.workspace.put(selection)
        self.active = FocusTarget(
            inspection_ref=inspection_ref,
            source_id=selection.source_id,
            target_ref=selection.target_ref,
            target_type=selection.target_type,
        )
        self.context = selection
        return selection

    def clear(self) -> None:
        self.active = None
        self.context = None
        self.workspace.clear()


def estimate_context_chars(selection: ContextSelection) -> int:
    total = len(selection.content)
    total += sum(
        len(str(key)) + len(str(value)) + 2
        for segment in selection.segments
        for key, value in segment.items()
    )
    total += sum(len(str(key)) + len(str(value)) + 2 for key, value in selection.metadata.items())
    total += len(selection.target_ref) + len(selection.source_id) + len(selection.mode)
    return total

