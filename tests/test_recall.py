"""Recall tests (Step 9 facade/integration).

Full coverage per IMPLEMENTATION_ORDER.md Step 9: byte-identical
restoration, recall-triggered eviction causal chain (trigger="recall",
displaced_by), silent no-op on a working id, ExpiredRecallError
mutates/logs nothing, extend-then-recall recovery path,
handle_recall_request validation, and unknown id raises.

Item sizes below are deliberately padded (not the bare minimum) so
that evicting the small item alone comfortably satisfies the budget
even after accounting for the page index's own token overhead once
that item is paged.
"""

from __future__ import annotations

import pytest

from aperture.errors import ExpiredRecallError, UnknownItemError
from aperture.items import EventKind, Provenance, SourceClass, Submission
from aperture.kernel import Aperture
from aperture.policy import Policy

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


def _setup_evicted_a(policy_overrides=None) -> tuple[Aperture, object, object]:
    """Submits A (small, ttl_turns=1, priority=5) and B (large, priority=0)
    under a budget where only A is over-budget-evicted (via Pass A/TTL) on
    the first balance() call; B stays working. Returns (kernel, result_a,
    result_b) at current_turn=1, right after that first balance().
    """
    policy = Policy(
        budget_total=130, reply_headroom=0, token_safety_margin=0.0, **(policy_overrides or {})
    )
    kernel = Aperture(policy)
    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    result_b = kernel.submit(_submission(content=_B_CONTENT, priority=0))
    kernel.next_turn()
    kernel.balance()
    return kernel, result_a, result_b


def test_recall_restores_byte_identical_content():
    kernel, result_a, result_b = _setup_evicted_a()
    assert result_a.item_id in kernel._page_store

    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)

    render = kernel.render()
    contents = [msg["content"] for msg in render.messages]
    assert f"[scratch #{result_a.item_id}]\n{_A_CONTENT}" in contents


def test_recall_triggered_eviction_logs_causal_chain():
    kernel, result_a, result_b = _setup_evicted_a()
    kernel.extend_ttl(result_a.item_id, turns=1000)

    report = kernel.recall(result_a.item_id)

    # B (lower priority number) is now the Pass C victim, tagged as
    # displaced by the recall of A.
    assert report.evicted_ids == [result_b.item_id]
    assert report.triggers[result_b.item_id] == "recall"
    assert report.displaced_by[result_b.item_id] == result_a.item_id

    events = kernel._log.events(kind=EventKind.evicted, item_id=result_b.item_id)
    assert len(events) == 1
    assert events[0].payload["trigger"] == "recall"
    assert events[0].payload["displaced_by"] == result_a.item_id


def test_recall_of_working_item_is_silent_no_op():
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="still working"))
    assert result.accepted

    events_before = kernel._log.export()
    report = kernel.recall(result.item_id)

    assert report.evicted_ids == []
    assert report.changed is False
    assert kernel._log.export() == events_before


def test_expired_recall_raises_and_mutates_nothing():
    kernel, result_a, result_b = _setup_evicted_a()

    events_before = kernel._log.export()
    working_len_before = len(kernel._working_set)
    page_len_before = len(kernel._page_store)

    with pytest.raises(ExpiredRecallError) as exc_info:
        kernel.recall(result_a.item_id)

    assert exc_info.value.item_id == result_a.item_id
    assert exc_info.value.expiry_turn == 1  # admitted_turn(0) + ttl_turns(1)
    assert kernel._log.export() == events_before
    assert len(kernel._working_set) == working_len_before
    assert len(kernel._page_store) == page_len_before


def test_extend_then_recall_succeeds():
    kernel, result_a, result_b = _setup_evicted_a()
    assert result_a.item_id in kernel._page_store

    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)  # must not raise

    assert result_a.item_id in kernel._working_set
    assert result_a.item_id not in kernel._page_store


def test_handle_recall_request_validation_and_success():
    kernel = Aperture(Policy())

    assert kernel.recall_request_schema() == {
        "name": "aperture_recall",
        "input": {"item_id": int},
    }

    assert kernel.handle_recall_request({}).startswith("error")
    assert kernel.handle_recall_request({"item_id": "not-an-int"}).startswith("error")
    assert kernel.handle_recall_request("not-a-dict").startswith("error")
    assert kernel.handle_recall_request({"item_id": 999}).startswith("error")

    kernel2, result_a, result_b = _setup_evicted_a()
    kernel2.extend_ttl(result_a.item_id, turns=1000)

    response = kernel2.handle_recall_request({"item_id": result_a.item_id})
    assert "recalled" in response
    assert result_a.item_id in kernel2._working_set


def test_recall_unknown_id_raises():
    kernel = Aperture(Policy())

    with pytest.raises(UnknownItemError):
        kernel.recall(9999)
