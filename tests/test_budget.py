"""Budget tests.

Step 4 adds only WorkingSet token-total cases (renderable vs
restricted, unpinned). Step 6 adds BudgetGovernor figure math
(effective_ceiling/reserved/contested_pool, restricted exclusion,
balance idempotence). Pass ordering/tie-breaks live in
tests/test_eviction.py and pin-overflow/eviction-immunity live in
tests/test_pins.py. This file is additive.
"""

from __future__ import annotations

from aperture.budget import BudgetGovernor
from aperture.items import ContextItem, ItemState, Provenance, SourceClass
from aperture.log import MutationLog
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.working_set import WorkingSet


def _make_item(item_id: int, *, source_class: SourceClass = SourceClass.scratch, token_size: int = 1, pinned: bool = False, mneme_meta=None, **overrides) -> ContextItem:
    fields = dict(
        id=item_id,
        source_class=source_class,
        content="content",
        token_size=token_size,
        priority=0,
        pinned=pinned,
        ttl_turns=None,
        provenance=Provenance(submitted_by="test"),
        index_line="line",
        state=ItemState.working,
        admitted_turn=0,
        last_rendered_turn=None,
        mneme_meta=mneme_meta,
    )
    fields.update(overrides)
    return ContextItem(**fields)


# --- Step 4: WorkingSet token totals ---------------------------------------


def test_class_totals_split_renderable_vs_restricted():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch, token_size=10))
    ws.insert(_make_item(2, source_class=SourceClass.conversation, token_size=20))
    ws.insert(
        _make_item(
            3,
            source_class=SourceClass.mneme_import,
            token_size=100,
            mneme_meta={"render_restricted": True},
        )
    )

    assert ws.class_totals() == {
        SourceClass.scratch: 10,
        SourceClass.conversation: 20,
    }
    assert ws.renderable_total() == 30
    assert ws.restricted_total() == 100


def test_restricted_requires_render_restricted_true_exactly():
    ws = WorkingSet()
    ws.insert(_make_item(1, token_size=5, mneme_meta={"render_restricted": False}))
    ws.insert(_make_item(2, token_size=7, mneme_meta={}))
    ws.insert(_make_item(3, token_size=11, mneme_meta=None))

    assert ws.restricted_total() == 0
    assert ws.renderable_total() == 23


def test_unpinned_totals_exclude_pinned_items():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch, token_size=10, pinned=True))
    ws.insert(_make_item(2, source_class=SourceClass.scratch, token_size=15, pinned=False))
    ws.insert(_make_item(3, source_class=SourceClass.conversation, token_size=20, pinned=True))

    assert ws.class_totals(unpinned_only=True) == {SourceClass.scratch: 15}
    assert ws.renderable_total(unpinned_only=True) == 15
    # Pinned items still count toward the (pinned-inclusive) renderable total.
    assert ws.renderable_total() == 45


def test_unpinned_totals_still_exclude_restricted_items():
    ws = WorkingSet()
    ws.insert(
        _make_item(
            1,
            token_size=50,
            pinned=False,
            mneme_meta={"render_restricted": True},
        )
    )
    ws.insert(_make_item(2, token_size=5, pinned=False))

    assert ws.class_totals(unpinned_only=True) == {SourceClass.scratch: 5}
    assert ws.renderable_total(unpinned_only=True) == 5


# --- Step 6: BudgetGovernor figures ----------------------------------------


def test_effective_ceiling_applies_safety_margin():
    policy = Policy(budget_total=1000, token_safety_margin=0.1)
    governor = BudgetGovernor(
        policy=policy,
        working_set=WorkingSet(),
        page_store=PageStore(),
        log=MutationLog(),
        page_index_cost=lambda: 0,
    )

    assert governor.effective_ceiling() == 900  # floor(1000 * 0.9)


def test_reserved_and_contested_pool_math():
    policy = Policy(budget_total=1000, reply_headroom=50, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(_make_item(1, token_size=100, pinned=True))

    governor = BudgetGovernor(
        policy=policy,
        working_set=ws,
        page_store=PageStore(),
        log=MutationLog(),
        page_index_cost=lambda: 30,
    )

    assert governor.effective_ceiling() == 1000
    assert governor.reserved() == 180  # 100 (pinned) + 30 (index) + 50 (headroom)
    assert governor.contested_pool() == 820


def test_restricted_items_excluded_from_reserved_and_contested_pool():
    policy = Policy(budget_total=1000, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(_make_item(1, token_size=100, pinned=True))
    ws.insert(
        _make_item(2, token_size=500, pinned=True, mneme_meta={"render_restricted": True})
    )
    ws.insert(
        _make_item(3, token_size=300, pinned=False, mneme_meta={"render_restricted": True})
    )

    governor = BudgetGovernor(
        policy=policy,
        working_set=ws,
        page_store=PageStore(),
        log=MutationLog(),
        page_index_cost=lambda: 0,
    )

    # Restricted items (pinned or not) contribute nothing to pinned_renderable_total,
    # reserved, or contested_pool.
    assert governor.pinned_renderable_total() == 100
    assert governor.reserved() == 100
    assert governor.contested_pool() == 900


def test_balance_is_idempotent():
    policy = Policy(budget_total=1000)
    ws = WorkingSet()
    ps = PageStore()
    log = MutationLog()
    ws.insert(_make_item(1, token_size=10))
    governor = BudgetGovernor(
        policy=policy, working_set=ws, page_store=ps, log=log, page_index_cost=lambda: 0
    )

    report_1 = governor.balance(current_turn=0)
    assert report_1.changed is False
    assert report_1.evicted_ids == []
    events_after_first = log.export()

    report_2 = governor.balance(current_turn=0)
    assert report_2.changed is False
    assert report_2.evicted_ids == []
    assert log.export() == events_after_first
