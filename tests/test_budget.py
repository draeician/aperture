"""Budget tests.

Step 4 adds only WorkingSet token-total cases (renderable vs
restricted, unpinned). The BudgetGovernor (balance/eviction) itself is
implemented in a later step; this file is additive.
"""

from __future__ import annotations

from aperture.items import ContextItem, ItemState, Provenance, SourceClass
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
