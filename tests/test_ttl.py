"""TTL tests (Step 9 facade/integration).

Full coverage per IMPLEMENTATION_ORDER.md Step 9: expiry flagging at
the exact boundary turn, extend_ttl on working and paged items,
non-positive turns validation, expired-flag clearing only when the new
horizon is in the future (via the recall recovery path), and
ttl_extended payload correctness.
"""

from __future__ import annotations

import pytest

from aperture.errors import ExpiredRecallError, InvalidItemError
from aperture.items import EventKind, Provenance, SourceClass, Submission
from aperture.kernel import Aperture
from aperture.policy import Policy

_A_CONTENT = "small item content here " + " ".join(f"pad{i}" for i in range(46))  # 49 words
_B_CONTENT = " ".join(f"word{i}" for i in range(100))  # 100 words


def _submission(source_class=SourceClass.scratch, content="hello", **overrides) -> Submission:
    overrides.setdefault("index_line", "x")
    return Submission(
        source_class=source_class,
        content=content,
        provenance=Provenance(submitted_by="test"),
        **overrides,
    )


def _setup_evicted_a(ttl_turns: int, turns_elapsed: int) -> tuple[Aperture, object, object]:
    """Submits A (small, given ttl_turns, priority=5) and B (large,
    priority=0); advances turns_elapsed turns; balances (Pass A pages A
    once it is expired, given the over-budget pressure from A+B).
    """
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)
    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=ttl_turns, priority=5))
    result_b = kernel.submit(_submission(content=_B_CONTENT, priority=0))
    for _ in range(turns_elapsed):
        kernel.next_turn()
    kernel.balance()
    return kernel, result_a, result_b


def test_expiry_flagging_at_exact_boundary_turn():
    kernel = Aperture(Policy(budget_total=1000))
    result = kernel.submit(_submission(content="hi there", ttl_turns=3))

    kernel.next_turn()
    kernel.next_turn()  # turn 2: 2 - 0 = 2 >= 3 is False, not yet expired
    kernel.balance()
    assert kernel._log.events(kind=EventKind.expired_flagged) == []

    kernel.next_turn()  # turn 3: 3 - 0 = 3 >= 3 is True, exactly at the boundary
    kernel.balance()
    events = kernel._log.events(kind=EventKind.expired_flagged, item_id=result.item_id)
    assert len(events) == 1
    assert events[0].turn == 3


def test_extend_ttl_on_working_item():
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="hello", ttl_turns=5))
    assert result.item_id in kernel._working_set

    kernel.extend_ttl(result.item_id, turns=10)

    item = kernel._working_set.get(result.item_id)
    assert item.ttl_turns == 15

    events = kernel._log.events(kind=EventKind.ttl_extended, item_id=result.item_id)
    assert len(events) == 1
    assert events[0].payload == {"old_ttl": 5, "new_ttl": 15}


def test_extend_ttl_on_paged_item():
    kernel, result_a, result_b = _setup_evicted_a(ttl_turns=5, turns_elapsed=10)
    assert result_a.item_id in kernel._page_store

    kernel.extend_ttl(result_a.item_id, turns=10)

    item = kernel._page_store.get(result_a.item_id)
    assert item.ttl_turns == 15

    events = kernel._log.events(kind=EventKind.ttl_extended, item_id=result_a.item_id)
    assert len(events) == 1
    assert events[0].payload == {"old_ttl": 5, "new_ttl": 15}


@pytest.mark.parametrize("turns", [0, -1, -100])
def test_non_positive_turns_raises_invalid_item_error(turns):
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="hello", ttl_turns=5))

    with pytest.raises(InvalidItemError):
        kernel.extend_ttl(result.item_id, turns)


def test_expired_flag_cleared_only_when_new_horizon_is_in_the_future():
    kernel, result_a, result_b = _setup_evicted_a(ttl_turns=5, turns_elapsed=10)
    assert result_a.item_id in kernel._page_store  # expired at turn 10 (10 - 0 >= 5)

    # A small extension (old_ttl=5 -> new_ttl=7) is not enough: still expired
    # at turn 10 (10 - 0 = 10 >= 7).
    kernel.extend_ttl(result_a.item_id, turns=2)
    with pytest.raises(ExpiredRecallError):
        kernel.recall(result_a.item_id)
    assert result_a.item_id in kernel._page_store  # recall did not mutate anything

    # A further extension (new_ttl=7 -> 17) pushes the horizon into the
    # future (10 - 0 = 10 >= 17 is False): recall now succeeds.
    kernel.extend_ttl(result_a.item_id, turns=10)
    kernel.recall(result_a.item_id)
    assert result_a.item_id in kernel._working_set


def test_ttl_extended_payload_correctness():
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="hello", ttl_turns=None))

    kernel.extend_ttl(result.item_id, turns=4)
    events = kernel._log.events(kind=EventKind.ttl_extended, item_id=result.item_id)
    assert events[-1].payload == {"old_ttl": None, "new_ttl": 4}

    kernel.extend_ttl(result.item_id, turns=6)
    events = kernel._log.events(kind=EventKind.ttl_extended, item_id=result.item_id)
    assert events[-1].payload == {"old_ttl": 4, "new_ttl": 10}
