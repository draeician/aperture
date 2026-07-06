"""Page index tests.

Step 4 adds only container-level store-ordering cases for WorkingSet
and PageStore (insert/membership, iteration order, duplicate-id
rejection, invariant assertions). The PageIndex rendering/collapse
behavior itself is implemented in a later step; this file is additive.
"""

from __future__ import annotations

import pytest

from aperture.items import ContextItem, ItemState, Provenance, SourceClass
from aperture.page_store import PageStore
from aperture.working_set import WorkingSet


def _make_item(item_id: int, *, state: ItemState = ItemState.working, content: str = "content", **overrides) -> ContextItem:
    fields = dict(
        id=item_id,
        source_class=SourceClass.scratch,
        content=content,
        token_size=1,
        priority=0,
        pinned=False,
        ttl_turns=None,
        provenance=Provenance(submitted_by="test"),
        index_line="line",
        state=state,
        admitted_turn=0,
        last_rendered_turn=None,
        mneme_meta=None,
    )
    fields.update(overrides)
    return ContextItem(**fields)


# --- Step 4: WorkingSet store ordering ------------------------------------


def test_working_set_insert_and_membership():
    ws = WorkingSet()
    item = _make_item(1)

    ws.insert(item)

    assert 1 in ws
    assert 2 not in ws
    assert ws.get(1) is item
    assert len(ws) == 1


def test_working_set_iteration_is_ascending_id():
    ws = WorkingSet()
    for item_id in (3, 1, 2):
        ws.insert(_make_item(item_id))

    assert [item.id for item in ws] == [1, 2, 3]


def test_working_set_rejects_duplicate_ids():
    ws = WorkingSet()
    ws.insert(_make_item(1))

    with pytest.raises(ValueError):
        ws.insert(_make_item(1))


def test_working_set_invariant_fails_if_state_not_working():
    ws = WorkingSet()
    ws.insert(_make_item(1, state=ItemState.paged))

    with pytest.raises(AssertionError):
        ws.check_invariants()


def test_working_set_invariant_passes_for_valid_state():
    ws = WorkingSet()
    ws.insert(_make_item(1))

    ws.check_invariants()


# --- Step 4: PageStore store ordering -------------------------------------


def test_page_store_insert_and_remove():
    store = PageStore()
    item = _make_item(1, state=ItemState.paged)

    store.insert(item)
    assert 1 in store
    assert store.get(1) is item

    removed = store.remove(1)
    assert removed is item
    assert 1 not in store
    assert len(store) == 0


def test_page_store_iteration_is_eviction_insertion_order():
    store = PageStore()
    for item_id in (3, 1, 2):
        store.insert(_make_item(item_id, state=ItemState.paged))

    assert [item.id for item in store] == [3, 1, 2]


def test_page_store_rejects_duplicate_ids():
    store = PageStore()
    store.insert(_make_item(1, state=ItemState.paged))

    with pytest.raises(ValueError):
        store.insert(_make_item(1, state=ItemState.paged))


def test_page_store_preserves_content_byte_identity():
    store = PageStore()
    item = _make_item(1, state=ItemState.paged, content="exact bytes, unchanged")

    store.insert(item)
    retrieved = store.get(1)

    assert retrieved is item
    assert retrieved.content is item.content


def test_page_store_invariant_fails_if_state_not_paged():
    store = PageStore()
    store.insert(_make_item(1, state=ItemState.working))

    with pytest.raises(AssertionError):
        store.check_invariants()


def test_page_store_invariant_passes_for_valid_state():
    store = PageStore()
    store.insert(_make_item(1, state=ItemState.paged))

    store.check_invariants()
