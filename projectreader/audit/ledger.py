"""A compact authoritative audit trail.

The ledger records observable actions and lifecycle results. It deliberately
does not contain private model reasoning, ranking state, or presentation state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One immutable event in a repository session."""

    sequence: int
    event_type: str
    target_ref: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "target_ref": self.target_ref,
            "data": dict(self.data),
        }


class AuditLedger:
    """Append-only in-memory audit ledger.

    Audit sequence numbers are session-local and deterministic.  Callers only
    receive copies of event collections, so they cannot rewrite history.
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._next_sequence = 1

    @property
    def last_sequence(self) -> int:
        return self._next_sequence - 1

    def record(
        self,
        event_type: str,
        *,
        target_ref: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        normalized_type = (event_type or "").strip()
        if not normalized_type:
            raise ValueError("event_type must not be empty")
        event = AuditEvent(
            sequence=self._next_sequence,
            event_type=normalized_type,
            target_ref=target_ref,
            data=dict(data or {}),
        )
        self._next_sequence += 1
        self._events.append(event)
        return event

    def all(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def by_type(self, event_type: str) -> tuple[AuditEvent, ...]:
        return tuple(item for item in self._events if item.event_type == event_type)

    def tail(self, limit: int = 20) -> tuple[AuditEvent, ...]:
        if limit < 0:
            raise ValueError("limit must not be negative")
        if limit == 0:
            return ()
        return tuple(self._events[-limit:])

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for event in self._events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return {
            "total": len(self._events),
            "by_type": dict(sorted(counts.items())),
        }
