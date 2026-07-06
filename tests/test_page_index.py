"""Page index tests.

Step 4 adds only container-level store-ordering cases for WorkingSet
and PageStore (insert/membership, iteration order, duplicate-id
rejection, invariant assertions). Step 7 adds PageIndex rendering,
mechanical index-line generation, collapse, and cost-accounting cases.
Recall/render/explain integration are later steps; this file is
additive.
"""

from __future__ import annotations

import pytest

from aperture.items import ContextItem, EventKind, ItemState, Provenance, SourceClass
from aperture.log import MutationLog
from aperture.page_index import PageIndex, mechanical_index_line
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.tokenizer import WhitespaceTokenizer
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


def test_working_set_remove_returns_and_removes_item():
    ws = WorkingSet()
    item = _make_item(1)
    ws.insert(item)

    removed = ws.remove(1)

    assert removed is item
    assert 1 not in ws
    assert len(ws) == 0


def test_working_set_remove_missing_id_raises_key_error():
    ws = WorkingSet()

    with pytest.raises(KeyError):
        ws.remove(1)


def test_working_set_iteration_is_ascending_id_after_remove():
    ws = WorkingSet()
    for item_id in (3, 1, 2, 4):
        ws.insert(_make_item(item_id))

    ws.remove(1)

    assert [item.id for item in ws] == [2, 3, 4]


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


# --- Step 7: PageIndex ----------------------------------------------------


class _FixedCountTokenizer:
    """Fake tokenizer with a distinctive, length-independent count."""

    def count(self, text: str) -> int:
        return 12345

    def split(self, text: str) -> list[str]:
        return text.split()


def test_page_index_entry_format():
    ps = PageStore()
    ps.insert(
        _make_item(
            7,
            state=ItemState.paged,
            source_class=SourceClass.conversation,
            index_line="hello world",
            token_size=42,
        )
    )
    policy = Policy(page_index_budget=10_000)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    text = pi.render()

    assert "#7 [conversation] hello world (42 tok)" in text.splitlines()


def test_mechanical_index_line_short_content_unchanged_besides_newlines():
    assert mechanical_index_line("short") == "short"
    assert mechanical_index_line("a\nb") == "a b"


def test_mechanical_index_line_collapses_newlines_and_hard_cuts_at_60():
    content = ("a" * 30) + "\n" + ("b" * 30) + "\n" + ("c" * 30)

    line = mechanical_index_line(content)

    assert len(line) == 60
    assert "\n" not in line
    assert line == (("a" * 30) + " " + ("b" * 29))


def test_page_index_block_starts_with_exact_header():
    ps = PageStore()
    ps.insert(_make_item(1, state=ItemState.paged))
    policy = Policy(page_index_budget=10_000)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    text = pi.render()

    assert text.splitlines()[0] == "--- PAGED CONTEXT (recall by id) ---"


def test_page_index_zero_paged_items_means_omitted_block_and_zero_cost():
    ps = PageStore()
    policy = Policy(page_index_budget=10_000)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    assert pi.render() == ""
    assert pi.cost() == 0


def test_page_index_entries_are_in_eviction_order():
    ps = PageStore()
    for item_id in (30, 10, 20):  # insertion order, deliberately not ascending id
        ps.insert(_make_item(item_id, state=ItemState.paged))
    policy = Policy(page_index_budget=10_000)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    text = pi.render()
    entry_lines = [line for line in text.splitlines() if line.startswith("#")]
    ids_in_order = [int(line.split()[0][1:]) for line in entry_lines]

    assert ids_in_order == [30, 10, 20]


def test_page_index_collapse_triggers_when_over_budget_with_exact_footer_format():
    ps = PageStore()
    for item_id in (1, 2, 3, 4, 5):
        ps.insert(_make_item(item_id, state=ItemState.paged, index_line="x", token_size=1))
    policy = Policy(page_index_budget=25)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    text = pi.render()

    assert text == "\n".join(
        [
            "--- PAGED CONTEXT (recall by id) ---",
            "+ 4 older paged items (ids 1..4; use explain)",
            "#5 [scratch] x (1 tok)",
        ]
    )
    assert pi.cost() == 21
    assert pi.collapsed_ids() == [1, 2, 3, 4]


def test_index_collapsed_log_includes_collapsed_ids_and_is_idempotent():
    ps = PageStore()
    for item_id in (1, 2, 3, 4, 5):
        ps.insert(_make_item(item_id, state=ItemState.paged, index_line="x", token_size=1))
    policy = Policy(page_index_budget=25)
    log = MutationLog()
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=log)

    pi.sync_collapse_log(turn=0)

    events = log.events(kind=EventKind.index_collapsed)
    assert len(events) == 1
    assert events[0].payload == {"collapsed_ids": [1, 2, 3, 4]}

    # No intervening mutation; a repeat call must not log again.
    pi.sync_collapse_log(turn=1)
    assert log.events(kind=EventKind.index_collapsed) == events


def test_collapsed_ids_remain_known_and_present_in_page_store():
    ps = PageStore()
    for item_id in (1, 2, 3, 4, 5):
        ps.insert(_make_item(item_id, state=ItemState.paged, index_line="x", token_size=1))
    policy = Policy(page_index_budget=25)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    collapsed = pi.collapsed_ids()

    assert collapsed == [1, 2, 3, 4]
    for item_id in collapsed:
        assert item_id in ps
        assert ps.get(item_id).state == ItemState.paged


def test_restricted_item_entry_always_uses_mneme_import_label():
    ps = PageStore()
    ps.insert(
        _make_item(
            9,
            state=ItemState.paged,
            source_class=SourceClass.scratch,  # deliberately not mneme_import
            index_line="a very secret detail that must never leak",
            token_size=77,
            mneme_meta={"render_restricted": True},
        )
    )
    policy = Policy(page_index_budget=10_000)
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=WhitespaceTokenizer(), log=MutationLog())

    text = pi.render()

    assert "#9 [mneme_import] (restricted) (77 tok)" in text.splitlines()
    assert "secret" not in text
    assert "scratch" not in text


def test_cost_accounting_uses_the_session_tokenizer():
    ps = PageStore()
    ps.insert(_make_item(1, state=ItemState.paged))
    policy = Policy(page_index_budget=10_000_000)  # large enough that no collapse occurs
    pi = PageIndex(page_store=ps, policy=policy, tokenizer=_FixedCountTokenizer(), log=MutationLog())

    assert pi.cost() == 12345
