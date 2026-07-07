"""Explain API tests.

Step 3 adds only log-only cases: append ordering, monotonic seq,
filtering by kind/item_id, immutability of returned data, and export
to plain data. Step 10 adds the full Explain API (item, absence,
render, budget, log, starved) and a ground-truth comparison harness
over a representative driven session. The Step 11 fuzz generator is
not implemented here.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from aperture.errors import UnknownItemError
from aperture.items import EventKind, ItemState, Provenance, SourceClass, Submission
from aperture.kernel import Aperture
from aperture.log import MutationLog
from aperture.policy import Policy


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


# --- Step 10: Explain API --------------------------------------------------

_A_CONTENT = "original exact content " + " ".join(f"pad{i}" for i in range(46))  # 49 words
_B_CONTENT = " ".join(f"word{i}" for i in range(100))  # 100 words


def _submission(source_class=SourceClass.scratch, content="hello", **overrides) -> Submission:
    overrides.setdefault("index_line", "x")
    return Submission(
        source_class=source_class,
        content=content,
        provenance=Provenance(submitted_by="test"),
        **overrides,
    )


def _setup_evicted_a() -> tuple[Aperture, object, object]:
    """Submits A (small, ttl_turns=1, priority=5) and B (large, priority=0)
    under a budget where only A is over-budget-evicted (via Pass A/TTL) on
    the first balance() call; B stays working.
    """
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)
    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    result_b = kernel.submit(_submission(content=_B_CONTENT, priority=0))
    kernel.next_turn()
    kernel.balance()
    return kernel, result_a, result_b


def test_explain_item_for_admitted_working_item():
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="hello there"))

    info = kernel.explain.item(result.item_id)

    assert info["item_id"] == result.item_id
    assert info["state"] == ItemState.working
    assert info["priority"] == 0
    assert info["pinned"] is False
    assert info["ttl_status"] == "no_ttl"
    assert any(e["kind"] == EventKind.admitted for e in info["events"])


def test_explain_item_for_paged_item():
    kernel, result_a, result_b = _setup_evicted_a()

    info = kernel.explain.item(result_a.item_id)

    assert info["state"] == ItemState.paged
    assert info["ttl_status"] == "expired"
    assert info["ttl_turns"] == 1
    kinds = [e["kind"] for e in info["events"]]
    assert EventKind.admitted in kinds
    assert EventKind.evicted in kinds


def test_explain_item_for_rejected_id():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)
    oversized = " ".join(f"word{i}" for i in range(50))
    result = kernel.submit(_submission(content=oversized))
    assert result.accepted is False

    info = kernel.explain.item(result.item_id)

    assert info["state"] == ItemState.rejected
    assert info["priority"] is None
    assert info["pinned"] is None
    assert len(info["events"]) == 1
    assert info["events"][0]["kind"] == EventKind.rejected
    assert info["events"][0]["payload"]["source_class"] == SourceClass.scratch


def test_explain_item_unknown_id_raises():
    kernel = Aperture(Policy())

    with pytest.raises(UnknownItemError):
        kernel.explain.item(9999)


def test_explain_absence_rejected():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)
    oversized = " ".join(f"word{i}" for i in range(50))
    result = kernel.submit(_submission(content=oversized))

    info = kernel.explain.absence(result.item_id)

    assert info["reason"] == "rejected"
    assert info["source_class"] == SourceClass.scratch
    assert info["token_size"] == 50
    assert "rejection_reason" in info


def test_explain_absence_paged_with_trigger_and_displaced_by():
    kernel, result_a, result_b = _setup_evicted_a()

    info = kernel.explain.absence(result_a.item_id)
    assert info == {"reason": "paged", "trigger": "ttl", "displaced_by": None}

    # recall A -> triggers an atomic rebalance that pages B instead, tagged
    # trigger="recall" with displaced_by=A's id.
    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)

    info_b = kernel.explain.absence(result_b.item_id)
    assert info_b == {"reason": "paged", "trigger": "recall", "displaced_by": result_a.item_id}


def test_explain_absence_render_restricted():
    kernel = Aperture(Policy())
    result = kernel.submit(
        _submission(content="restricted content", mneme_meta={"render_restricted": True})
    )

    assert kernel.explain.absence(result.item_id) == {"reason": "render_restricted"}
    assert result.item_id in kernel._working_set  # still present, just excluded from render


def test_explain_absence_unknown_id_raises():
    kernel = Aperture(Policy())

    with pytest.raises(UnknownItemError):
        kernel.explain.absence(9999)


def test_explain_render_last_manifest_and_totals():
    kernel = Aperture(Policy())
    kernel.submit(_submission(content="one", source_class=SourceClass.scratch))
    kernel.balance()
    kernel.render()

    info = kernel.explain.render("last")

    assert info["found"] is True
    assert info["manifest"] == [1]
    assert info["class_totals"] == {SourceClass.scratch: 1}


def test_explain_render_diff_added_removed_recalled():
    kernel = Aperture(Policy())
    kernel.submit(_submission(content="one"))
    kernel.balance()
    kernel.render()

    kernel.submit(_submission(content="two"))
    kernel.balance()
    kernel.render()
    info = kernel.explain.render("last")

    assert info["diff"]["added"] == [2]
    assert info["diff"]["removed"] == []
    assert info["diff"]["recalled"] == []


def test_explain_render_diff_reports_recalled_ids():
    kernel, result_a, result_b = _setup_evicted_a()
    kernel.render()  # first render: only B is working

    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)  # this also pages B (trigger="recall")
    kernel.render()  # second render: only A is working again

    info = kernel.explain.render("last")

    assert info["diff"]["recalled"] == [result_a.item_id]
    assert info["diff"]["added"] == [result_a.item_id]
    assert info["diff"]["removed"] == [result_b.item_id]


def test_explain_render_no_render_yet_returns_empty_result():
    kernel = Aperture(Policy())

    info = kernel.explain.render("last")

    assert info == {
        "found": False,
        "manifest": [],
        "class_totals": {},
        "diff": {"added": [], "removed": [], "recalled": []},
    }


def test_explain_budget_ceiling_reserved_and_contested_pool():
    policy = Policy(budget_total=1000, reply_headroom=50, token_safety_margin=0.0)
    kernel = Aperture(policy)
    kernel.submit(_submission(content="pinned item", pinned=True))

    info = kernel.explain.budget()

    assert info["effective_ceiling"] == 1000
    assert info["reserved"]["headroom"] == 50
    assert info["reserved"]["pinned"] >= 0
    assert info["reserved"]["index"] == 0
    assert info["contested_pool"] == (
        info["effective_ceiling"] - info["reserved"]["pinned"] - info["reserved"]["index"] - info["reserved"]["headroom"]
    )


def test_explain_budget_reports_restricted_nonrendered_tokens_separately():
    kernel = Aperture(Policy())
    kernel.submit(_submission(content="visible one two three"))
    kernel.submit(
        _submission(
            content="restricted alpha beta gamma",
            mneme_meta={"render_restricted": True},
        )
    )

    info = kernel.explain.budget()

    assert info["restricted_nonrendered_tokens"] == 4
    assert SourceClass.scratch in info["class_totals"]
    assert info["class_totals"][SourceClass.scratch] == 4  # only the visible item counts


def test_explain_log_filtering_by_kind():
    kernel = Aperture(Policy())
    kernel.submit(_submission(content="one"))
    kernel.submit(_submission(content="two"))

    events = kernel.explain.log(kind=EventKind.admitted)

    assert len(events) == 2
    assert all(e["kind"] == EventKind.admitted for e in events)


def test_explain_log_filtering_by_item_id():
    kernel = Aperture(Policy())
    kernel.submit(_submission(content="one"))
    result_2 = kernel.submit(_submission(content="two"))
    kernel.extend_ttl(result_2.item_id, turns=5)

    events = kernel.explain.log(item_id=result_2.item_id)

    assert len(events) == 2
    assert {e["kind"] for e in events} == {EventKind.admitted, EventKind.ttl_extended}


def test_explain_log_returned_data_cannot_mutate_internal_log_state():
    kernel = Aperture(Policy())
    kernel.submit(_submission(content="one"))

    events = kernel.explain.log()
    events.append("intruder")
    events[0]["payload"]["injected"] = True

    fresh = kernel.explain.log()
    assert fresh != ["intruder"]
    assert "injected" not in fresh[0]["payload"]


def test_explain_starved_includes_old_never_rendered_renderable_working_items():
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="never rendered"))
    kernel.next_turn()
    kernel.next_turn()  # current_turn - admitted_turn == 2

    assert kernel.explain.starved() == [result.item_id]


def test_explain_starved_excludes_restricted_paged_rejected_and_recent_items():
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    # paged (via TTL eviction under budget pressure)
    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    result_b = kernel.submit(_submission(content=_B_CONTENT, priority=0))
    kernel.next_turn()
    kernel.balance()
    assert result_a.item_id in kernel._page_store

    # restricted
    result_restricted = kernel.submit(
        _submission(content="restricted", mneme_meta={"render_restricted": True})
    )

    # rejected
    policy_tight = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    kernel_tight = Aperture(policy_tight)
    oversized = " ".join(f"word{i}" for i in range(50))
    result_rejected = kernel_tight.submit(_submission(content=oversized))
    assert result_rejected.accepted is False

    # recently admitted (current_turn - admitted_turn < 2)
    result_recent = kernel.submit(_submission(content="recent"))

    for _ in range(5):
        kernel.next_turn()

    starved = kernel.explain.starved()

    assert result_a.item_id not in starved  # paged
    assert result_restricted.item_id not in starved  # restricted
    assert result_rejected.item_id not in starved  # rejected (different kernel entirely)
    assert result_recent.item_id in starved  # by now old enough
    assert result_b.item_id in starved  # working, renderable, never rendered, old enough


def test_explain_ground_truth_matches_over_representative_driven_session():
    """Independently re-derive expected per-item event history from the raw
    exported log and confirm explain.log()/explain.item() match exactly,
    for a small hand-driven (not fuzzed) representative session.
    """
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    result_b = kernel.submit(_submission(content=_B_CONTENT, priority=0))
    kernel.next_turn()
    kernel.balance()
    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)
    kernel.render()

    all_events = kernel._log.export()

    for item_id in (result_a.item_id, result_b.item_id):
        ground_truth = [e for e in all_events if e["item_id"] == item_id]
        assert kernel.explain.log(item_id=item_id) == ground_truth
        assert kernel.explain.item(item_id)["events"] == ground_truth

    for kind in (EventKind.admitted, EventKind.evicted, EventKind.recalled, EventKind.ttl_extended):
        ground_truth = [e for e in all_events if e["kind"] == kind]
        assert kernel.explain.log(kind=kind) == ground_truth
