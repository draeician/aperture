"""Append-only session mutation log."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from aperture.items import EventKind


@dataclass(frozen=True)
class MutationEvent:
    seq: int
    turn: int
    kind: EventKind
    item_id: int | None
    payload: Mapping[str, object]


class MutationLog:
    """Append-only log of MutationEvents with monotonic sequence numbers.

    Exposes no mutation or deletion API: once appended, an event cannot
    be changed or removed. Filtered reads and export() always return
    fresh, independent data so callers cannot reach back into internal
    log state.
    """

    def __init__(self) -> None:
        self._events: list[MutationEvent] = []
        self._seq_counter = 0

    def append(
        self,
        *,
        turn: int,
        kind: EventKind,
        item_id: int | None,
        payload: Mapping[str, object],
    ) -> MutationEvent:
        self._seq_counter += 1
        event = MutationEvent(
            seq=self._seq_counter,
            turn=turn,
            kind=kind,
            item_id=item_id,
            payload=MappingProxyType(dict(payload)),
        )
        self._events.append(event)
        return event

    def events(
        self,
        kind: EventKind | None = None,
        item_id: int | None = None,
    ) -> list[MutationEvent]:
        return [
            event
            for event in self._events
            if (kind is None or event.kind == kind)
            and (item_id is None or event.item_id == item_id)
        ]

    def export(self) -> list[dict[str, object]]:
        return [
            {
                "seq": event.seq,
                "turn": event.turn,
                "kind": event.kind,
                "item_id": event.item_id,
                "payload": dict(event.payload),
            }
            for event in self._events
        ]
