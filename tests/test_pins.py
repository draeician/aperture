"""Pin tests.

Step 6 adds only pin-overflow and pinned-eviction-immunity cases for
BudgetGovernor.balance(). Recall-related pin behavior is a later step;
this file is additive.
"""

from __future__ import annotations

import pytest

from aperture.budget import BudgetGovernor
from aperture.errors import PinOverflowError
from aperture.items import ContextItem, ItemState, Provenance, SourceClass
from aperture.log import MutationLog
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.working_set import WorkingSet


def _make_item(
    item_id: int,
    *,
    source_class: SourceClass = SourceClass.scratch,
    token_size: int = 1,
    priority: int = 0,
    pinned: bool = False,
    **overrides,
) -> ContextItem:
    fields = dict(
        id=item_id,
        source_class=source_class,
        content="content",
        token_size=token_size,
        priority=priority,
        pinned=pinned,
        ttl_turns=None,
        provenance=Provenance(submitted_by="test"),
        index_line="line",
        state=ItemState.working,
        admitted_turn=0,
        last_rendered_turn=None,
        mneme_meta=None,
    )
    fields.update(overrides)
    return ContextItem(**fields)


def test_pin_overflow_raises_before_eviction_and_logs_nothing():
    policy = Policy(budget_total=100, reply_headroom=0)
    ws = WorkingSet()
    ps = PageStore()
    log = MutationLog()
    ws.insert(_make_item(1, token_size=150, pinned=True))
    governor = BudgetGovernor(
        policy=policy, working_set=ws, page_store=ps, log=log, page_index_cost=lambda: 0
    )

    with pytest.raises(PinOverflowError) as exc_info:
        governor.balance(current_turn=0)

    assert exc_info.value.pinned_total == 150
    assert exc_info.value.ceiling == 100
    assert log.export() == []
    assert 1 in ws
    assert len(ps) == 0


def test_pinned_items_never_evicted():
    policy = Policy(budget_total=50, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ps = PageStore()
    log = MutationLog()
    ws.insert(_make_item(1, token_size=40, pinned=True))
    ws.insert(_make_item(2, token_size=40, pinned=False))
    governor = BudgetGovernor(
        policy=policy, working_set=ws, page_store=ps, log=log, page_index_cost=lambda: 0
    )

    report = governor.balance(current_turn=0)

    assert report.evicted_ids == [2]
    assert report.triggers[2] == "global"
    assert 1 in ws
    assert ws.get(1).pinned is True
    assert 2 not in ws
