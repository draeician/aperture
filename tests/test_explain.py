"""Explain API tests.

Step 3 adds only log-only cases: append ordering, monotonic seq,
filtering by kind/item_id, immutability of returned data, and export
to plain data. The explain.* API itself is implemented in a later
step; this file is additive.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from aperture.items import EventKind
from aperture.log import MutationLog


# --- Step 3: MutationLog ----------------------------------------------


def test_append_returns_events_in_append_order():
    log = MutationLog()
    e1 = log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={})
    e2 = log.append(turn=0, kind=EventKind.admitted, item_id=2, payload={})
    e3 = log.append(turn=1, kind=EventKind.evicted, item_id=1, payload={})

    assert log.events() == [e1, e2, e3]


def test_seq_values_are_monotonic_and_deterministic():
    log = MutationLog()
    events = [
        log.append(turn=0, kind=EventKind.admitted, item_id=i, payload={})
        for i in range(5)
    ]
    seqs = [event.seq for event in events]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_filter_by_kind():
    log = MutationLog()
    a = log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={})
    log.append(turn=0, kind=EventKind.rejected, item_id=2, payload={})
    b = log.append(turn=1, kind=EventKind.admitted, item_id=3, payload={})

    assert log.events(kind=EventKind.admitted) == [a, b]


def test_filter_by_item_id():
    log = MutationLog()
    a = log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={})
    log.append(turn=0, kind=EventKind.evicted, item_id=2, payload={})
    b = log.append(turn=1, kind=EventKind.recalled, item_id=1, payload={})

    assert log.events(item_id=1) == [a, b]


def test_filter_by_kind_and_item_id_combined():
    log = MutationLog()
    target = log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={})
    log.append(turn=0, kind=EventKind.evicted, item_id=1, payload={})
    log.append(turn=1, kind=EventKind.admitted, item_id=2, payload={})

    assert log.events(kind=EventKind.admitted, item_id=1) == [target]


def test_events_returns_independent_list():
    log = MutationLog()
    log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={})

    result = log.events()
    result.append("intruder")

    assert len(log.events()) == 1


def test_payload_is_immutable_and_defensively_copied():
    log = MutationLog()
    source_payload = {"a": 1}
    event = log.append(turn=0, kind=EventKind.admitted, item_id=1, payload=source_payload)

    # Mutating the caller's original dict after append must not reach the stored event.
    source_payload["a"] = 999
    assert event.payload["a"] == 1

    # The stored payload mapping itself must reject mutation.
    with pytest.raises(TypeError):
        event.payload["a"] = 2


def test_mutation_event_is_frozen():
    log = MutationLog()
    event = log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={})

    with pytest.raises(FrozenInstanceError):
        event.seq = 999


def test_export_returns_plain_data():
    log = MutationLog()
    log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={"k": "v"})

    exported = log.export()

    assert exported == [
        {"seq": 1, "turn": 0, "kind": EventKind.admitted, "item_id": 1, "payload": {"k": "v"}}
    ]
    assert isinstance(exported[0]["payload"], dict)
    assert not isinstance(exported[0]["payload"], MappingProxyType)


def test_export_is_independent_of_internal_state():
    log = MutationLog()
    log.append(turn=0, kind=EventKind.admitted, item_id=1, payload={"k": "v"})

    exported = log.export()
    exported[0]["payload"]["k"] = "mutated"
    exported.append("intruder")

    fresh = log.export()
    assert fresh == [
        {"seq": 1, "turn": 0, "kind": EventKind.admitted, "item_id": 1, "payload": {"k": "v"}}
    ]


def test_log_exposes_no_mutation_or_deletion_api():
    log = MutationLog()
    for forbidden in ("remove", "delete", "clear", "pop", "edit", "update"):
        assert not hasattr(log, forbidden)
