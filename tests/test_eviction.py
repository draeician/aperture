"""Eviction tests.

Step 6 adds only BudgetGovernor.balance() pass-ordering, tie-break,
immunity, page-index-cost recomputation, and determinism cases.
PageIndex itself is a later step; page_index_cost is a fake/injected
provider throughout this file. This file is additive.
"""

from __future__ import annotations

from aperture.budget import BudgetGovernor
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
    ttl_turns=None,
    admitted_turn: int = 0,
    last_rendered_turn=None,
    mneme_meta=None,
    **overrides,
) -> ContextItem:
    fields = dict(
        id=item_id,
        source_class=source_class,
        content="content",
        token_size=token_size,
        priority=priority,
        pinned=pinned,
        ttl_turns=ttl_turns,
        provenance=Provenance(submitted_by="test"),
        index_line="line",
        state=ItemState.working,
        admitted_turn=admitted_turn,
        last_rendered_turn=last_rendered_turn,
        mneme_meta=mneme_meta,
    )
    fields.update(overrides)
    return ContextItem(**fields)


def _governor(policy, ws, ps=None, log=None, page_index_cost=None):
    return BudgetGovernor(
        policy=policy,
        working_set=ws,
        page_store=ps if ps is not None else PageStore(),
        log=log if log is not None else MutationLog(),
        page_index_cost=page_index_cost if page_index_cost is not None else (lambda: 0),
    )


# --- Pass ordering ----------------------------------------------------


def test_ttl_pass_happens_before_subbudget_and_global():
    policy = Policy(budget_total=20, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    # Expired, but a "less preferred" priority under Pass B/C tie-break.
    ws.insert(_make_item(1, token_size=15, ttl_turns=1, admitted_turn=0, priority=5))
    # Never expires, and the "most preferred" priority under Pass B/C tie-break.
    ws.insert(_make_item(2, token_size=10, ttl_turns=None, priority=0))
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=5)

    # Pass A resolves the over-budget condition by evicting the TTL-expired
    # item first, so Pass B/C never runs at all -- item 2 is untouched even
    # though it would otherwise be Pass C's first pick.
    assert report.evicted_ids == [1]
    assert report.triggers[1] == "ttl"
    assert 2 in ws


def test_subbudget_pass_respects_class_order():
    policy = Policy(
        budget_total=100,
        reply_headroom=0,
        token_safety_margin=0.0,
        class_subbudgets={SourceClass.scratch: 0.5, SourceClass.tool_output: 0.5},
    )
    ws = WorkingSet()
    ws.insert(_make_item(10, source_class=SourceClass.scratch, token_size=40, priority=0))
    ws.insert(_make_item(11, source_class=SourceClass.scratch, token_size=40, priority=1))
    ws.insert(_make_item(20, source_class=SourceClass.tool_output, token_size=40, priority=0))
    ws.insert(_make_item(21, source_class=SourceClass.tool_output, token_size=40, priority=1))
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=0)

    # scratch (first in class_eviction_order) is fully resolved before
    # tool_output is touched at all.
    assert report.evicted_ids == [10, 20]
    assert report.triggers == {10: "subbudget", 20: "subbudget"}


def test_global_pass_respects_class_order():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(_make_item(10, source_class=SourceClass.scratch, token_size=8, priority=0))
    ws.insert(_make_item(11, source_class=SourceClass.scratch, token_size=8, priority=1))
    # Much lower (more eviction-preferred) priority number, but in a class
    # that is later in class_eviction_order.
    ws.insert(
        _make_item(20, source_class=SourceClass.tool_output, token_size=100, priority=-100)
    )
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=0)

    # scratch class is fully drained before tool_output is touched, despite
    # item 20's much more "eviction-preferred" priority number.
    assert report.evicted_ids == [10, 11, 20]
    assert all(trigger == "global" for trigger in report.triggers.values())


# --- Tie-break key ------------------------------------------------------


def _tie_break_scenario():
    policy = Policy(budget_total=1, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(_make_item(3, priority=0, token_size=10))  # largest size at priority 0
    ws.insert(_make_item(1, priority=0, token_size=5))  # tied with 2 and 4
    ws.insert(_make_item(2, priority=0, token_size=5))  # tied with 1 and 4
    ws.insert(_make_item(4, priority=0, token_size=5, last_rendered_turn=3))
    ws.insert(_make_item(5, priority=1, token_size=100))  # worst (highest) priority number
    return _governor(policy, ws)


def test_within_class_tie_break_key_exact_order():
    governor = _tie_break_scenario()

    report = governor.balance(current_turn=0)

    # ascending priority first (5 last); among priority 0: descending
    # token_size (3 first); among remaining ties: ascending last_rendered_turn
    # with None as -1 (1 and 2 before 4); full tie: ascending id (1 before 2).
    assert report.evicted_ids == [3, 1, 2, 4, 5]


def test_deterministic_eviction_sequence_on_repeated_constructed_states():
    governor_a = _tie_break_scenario()
    governor_b = _tie_break_scenario()

    report_a = governor_a.balance(current_turn=0)
    report_b = governor_b.balance(current_turn=0)

    assert report_a.evicted_ids == report_b.evicted_ids == [3, 1, 2, 4, 5]
    assert report_a.triggers == report_b.triggers
    assert report_a.displaced_by == report_b.displaced_by


# --- TTL ordering --------------------------------------------------------


def test_ttl_ordering_oldest_admitted_then_lower_id():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(_make_item(3, ttl_turns=1, admitted_turn=5, token_size=5))
    ws.insert(_make_item(1, ttl_turns=1, admitted_turn=2, token_size=5))
    ws.insert(_make_item(2, ttl_turns=1, admitted_turn=2, token_size=5))
    ws.insert(_make_item(4, ttl_turns=1, admitted_turn=10, token_size=5))
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=100)

    # oldest admitted_turn first; ids 1 and 2 tie on admitted_turn=2, so id 1
    # (lower id) goes first. Stops once total (10) fits contested_pool (10).
    assert report.evicted_ids == [1, 2]
    assert all(trigger == "ttl" for trigger in report.triggers.values())


# --- Immunity ------------------------------------------------------------


def test_system_items_never_evicted():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.system, token_size=1000))
    ws.insert(_make_item(2, source_class=SourceClass.scratch, token_size=5))
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=0)

    assert report.evicted_ids == [2]
    assert 1 in ws
    assert ws.get(1).state == ItemState.working


def test_restricted_items_skipped_in_subbudget_and_global_passes():
    policy = Policy(
        budget_total=15,
        reply_headroom=0,
        token_safety_margin=0.0,
        class_subbudgets={SourceClass.scratch: 0.01},
    )
    ws = WorkingSet()
    ws.insert(
        _make_item(
            1,
            source_class=SourceClass.scratch,
            token_size=9999,
            mneme_meta={"render_restricted": True},
        )
    )
    ws.insert(_make_item(2, source_class=SourceClass.scratch, token_size=10))
    ws.insert(
        _make_item(
            3,
            source_class=SourceClass.tool_output,
            token_size=9999,
            mneme_meta={"render_restricted": True},
        )
    )
    ws.insert(_make_item(4, source_class=SourceClass.tool_output, token_size=20))
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=0)

    # item 2 evicted by Pass B (scratch subbudget), item 4 by Pass C
    # (global); the restricted items (1, 3) are never touched in either pass.
    assert report.evicted_ids == [2, 4]
    assert report.triggers == {2: "subbudget", 4: "global"}
    assert 1 in ws
    assert 3 in ws
    assert len(ws) == 2


def test_restricted_expired_item_can_be_evicted_in_pass_a():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ws.insert(
        _make_item(
            1,
            token_size=5,
            ttl_turns=1,
            admitted_turn=0,
            mneme_meta={"render_restricted": True},
        )
    )
    ws.insert(_make_item(2, token_size=20, ttl_turns=None))
    governor = _governor(policy, ws)

    report = governor.balance(current_turn=5)

    assert 1 in report.evicted_ids
    assert report.triggers[1] == "ttl"


# --- page_index_cost recomputation and termination ------------------------


class _CountingCost:
    def __init__(self, page_store: PageStore, per_item: int) -> None:
        self.page_store = page_store
        self.per_item = per_item
        self.calls = 0

    def __call__(self) -> int:
        self.calls += 1
        return self.per_item * len(self.page_store)


def test_page_index_cost_provider_is_recomputed_after_each_eviction():
    policy = Policy(budget_total=30, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ps = PageStore()
    ws.insert(_make_item(1, token_size=15))
    ws.insert(_make_item(2, token_size=15))
    ws.insert(_make_item(3, token_size=15))
    cost = _CountingCost(ps, per_item=5)
    governor = _governor(policy, ws, ps=ps, page_index_cost=cost)

    report = governor.balance(current_turn=0)

    # Without recomputation, contested_pool would stay at 30 and a single
    # eviction (45 - 15 = 30) would suffice. Because cost grows by 5 per
    # paged item, contested_pool shrinks after each eviction (30 -> 25 ->
    # 20), so a second eviction is required to fit.
    assert report.evicted_ids == [1, 2]
    assert cost.calls >= len(report.evicted_ids) + 1


def test_termination_under_simulated_index_growth():
    policy = Policy(budget_total=100, reply_headroom=0, token_safety_margin=0.0)
    ws = WorkingSet()
    ps = PageStore()
    ws.insert(_make_item(1, token_size=50))
    ws.insert(_make_item(2, token_size=50))
    ws.insert(_make_item(3, token_size=50))
    # Adversarial: cost grows so fast that contested_pool goes deeply
    # negative after the first eviction, meaning the "over budget" check
    # can never resolve to False again for the rest of this run.
    cost = _CountingCost(ps, per_item=1_000_000)
    governor = _governor(policy, ws, ps=ps, page_index_cost=cost)

    report = governor.balance(current_turn=0)

    # balance() must still terminate: once every eligible candidate has
    # been evicted, Pass C runs out of classes and stops regardless of
    # whether the invariant was ever actually satisfied.
    assert report.evicted_ids == [1, 2, 3]
    assert len(ws) == 0
    assert len(ps) == 3
