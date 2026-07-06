"""Renderer tests.

Step 8 adds the full Renderer test suite: balanced-state precondition,
fixed ordering, role mapping, label prefixes, page-index-slot
placement, restricted-item exclusion, last_rendered_turn effects, the
rendered log event, render purity/idempotent-output, and confirmation
that render() never triggers PageIndex collapse logging. Kernel/explain
integration are later steps; this file covers Step 8 only.
"""

from __future__ import annotations

import pytest

from aperture.errors import UnbalancedError
from aperture.items import ContextItem, EventKind, ItemState, Provenance, SourceClass
from aperture.log import MutationLog
from aperture.page_index import PageIndex
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.renderer import Renderer
from aperture.tokenizer import WhitespaceTokenizer
from aperture.working_set import WorkingSet


def _make_item(
    item_id: int,
    *,
    source_class: SourceClass = SourceClass.scratch,
    content: str = "content",
    token_size: int = 1,
    priority: int = 0,
    pinned: bool = False,
    ttl_turns=None,
    admitted_turn: int = 0,
    last_rendered_turn=None,
    mneme_meta=None,
    index_line: str = "line",
    state: ItemState = ItemState.working,
    **overrides,
) -> ContextItem:
    fields = dict(
        id=item_id,
        source_class=source_class,
        content=content,
        token_size=token_size,
        priority=priority,
        pinned=pinned,
        ttl_turns=ttl_turns,
        provenance=Provenance(submitted_by="test"),
        index_line=index_line,
        state=state,
        admitted_turn=admitted_turn,
        last_rendered_turn=last_rendered_turn,
        mneme_meta=mneme_meta,
    )
    fields.update(overrides)
    return ContextItem(**fields)


def _empty_page_index() -> PageIndex:
    return PageIndex(
        page_store=PageStore(), policy=Policy(), tokenizer=WhitespaceTokenizer(), log=MutationLog()
    )


class _SpyPageIndex:
    """Wraps a real PageIndex, recording whether sync_collapse_log was called."""

    def __init__(self, real: PageIndex) -> None:
        self._real = real
        self.sync_collapse_log_calls = 0

    def render(self) -> str:
        return self._real.render()

    def cost(self) -> int:
        return self._real.cost()

    def sync_collapse_log(self, turn: int) -> None:
        self.sync_collapse_log_calls += 1
        self._real.sync_collapse_log(turn)


def test_render_raises_unbalanced_error_when_unbalanced():
    renderer = Renderer(
        working_set=WorkingSet(),
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: False,
    )

    with pytest.raises(UnbalancedError):
        renderer.render(current_turn=0)


def test_render_does_not_evict_even_when_over_budget():
    ws = WorkingSet()
    ws.insert(_make_item(1, token_size=10_000))
    policy = Policy(budget_total=1)
    renderer = Renderer(
        working_set=ws,
        policy=policy,
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    renderer.render(current_turn=0)

    assert 1 in ws
    assert len(ws) == 1


def test_render_follows_policy_render_order():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.conversation, content="conv"))
    ws.insert(_make_item(2, source_class=SourceClass.scratch, content="scr"))
    ws.insert(_make_item(3, source_class=SourceClass.system, content="sys"))
    policy = Policy(
        render_order=(
            SourceClass.system,
            SourceClass.scratch,
            SourceClass.conversation,
            SourceClass.tool_output,
            SourceClass.mneme_import,
            SourceClass.user_fact,
            SourceClass.page_index,
        )
    )
    renderer = Renderer(
        working_set=ws,
        policy=policy,
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result = renderer.render(current_turn=0)

    assert result.manifest == [3, 2, 1]


def test_render_ascending_id_within_class():
    ws = WorkingSet()
    ws.insert(_make_item(5, source_class=SourceClass.scratch))
    ws.insert(_make_item(2, source_class=SourceClass.scratch))
    ws.insert(_make_item(9, source_class=SourceClass.scratch))
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result = renderer.render(current_turn=0)

    assert result.manifest == [2, 5, 9]


def test_render_role_mapping():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.system, content="sys content"))
    ws.insert(_make_item(2, source_class=SourceClass.scratch, content="scratch content"))
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result = renderer.render(current_turn=0)

    roles = {msg["content"].splitlines()[0]: msg["role"] for msg in result.messages}
    assert roles["[system #1]"] == "system"
    assert roles["[scratch #2]"] == "user"


def test_render_label_prefix_format_exact():
    ws = WorkingSet()
    ws.insert(_make_item(5, source_class=SourceClass.tool_output, content="the actual content"))
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result = renderer.render(current_turn=0)

    assert result.messages == [{"role": "user", "content": "[tool_output #5]\nthe actual content"}]


def test_render_page_index_appears_in_its_slot():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.system, content="sys"))
    ws.insert(_make_item(2, source_class=SourceClass.scratch, content="scr"))

    page_store = PageStore()
    page_store.insert(
        _make_item(99, state=ItemState.paged, source_class=SourceClass.scratch, index_line="paged")
    )
    page_index = PageIndex(
        page_store=page_store,
        policy=Policy(page_index_budget=10_000),
        tokenizer=WhitespaceTokenizer(),
        log=MutationLog(),
    )

    policy = Policy(
        render_order=(
            SourceClass.system,
            SourceClass.page_index,
            SourceClass.scratch,
            SourceClass.conversation,
            SourceClass.tool_output,
            SourceClass.mneme_import,
            SourceClass.user_fact,
        )
    )
    renderer = Renderer(
        working_set=ws, policy=policy, page_index=page_index, log=MutationLog(), is_balanced=lambda: True
    )

    result = renderer.render(current_turn=0)

    assert len(result.messages) == 3
    assert result.messages[0]["content"].startswith("[system #1]")
    assert result.messages[1]["content"] == page_index.render()
    assert result.messages[1]["role"] == "user"
    assert result.messages[2]["content"].startswith("[scratch #2]")
    # the page index block is not a ContextItem: absent from the manifest
    assert result.manifest == [1, 2]


def test_render_omits_empty_page_index_block():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch))
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result = renderer.render(current_turn=0)

    assert all("PAGED CONTEXT" not in msg["content"] for msg in result.messages)
    assert len(result.messages) == 1


def test_restricted_items_absent_from_output_manifest_and_totals():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch, content="visible", token_size=10))
    ws.insert(
        _make_item(
            2,
            source_class=SourceClass.mneme_import,
            content="SECRET_RESTRICTED_CONTENT",
            token_size=500,
            mneme_meta={"render_restricted": True},
        )
    )
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result = renderer.render(current_turn=0)

    assert 2 not in result.manifest
    assert result.manifest == [1]
    assert all("SECRET_RESTRICTED_CONTENT" not in msg["content"] for msg in result.messages)
    assert result.total_tokens == 10


def test_restricted_items_remain_in_working_set_after_render():
    ws = WorkingSet()
    ws.insert(
        _make_item(2, source_class=SourceClass.mneme_import, mneme_meta={"render_restricted": True})
    )
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    renderer.render(current_turn=0)

    assert 2 in ws
    assert ws.get(2).state == ItemState.working


def test_last_rendered_turn_updates_only_for_rendered_real_items():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch, last_rendered_turn=None))
    ws.insert(
        _make_item(
            2,
            source_class=SourceClass.mneme_import,
            mneme_meta={"render_restricted": True},
            last_rendered_turn=None,
        )
    )
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    renderer.render(current_turn=7)

    assert ws.get(1).last_rendered_turn == 7
    assert ws.get(2).last_rendered_turn is None


def test_rendered_log_event_payload_contains_manifest_and_total_tokens():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch, token_size=10))
    ws.insert(_make_item(2, source_class=SourceClass.scratch, token_size=15))
    log = MutationLog()
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=log,
        is_balanced=lambda: True,
    )

    renderer.render(current_turn=3)

    events = log.events(kind=EventKind.rendered)
    assert len(events) == 1
    assert events[0].turn == 3
    assert events[0].item_id is None
    assert events[0].payload == {"manifest": [1, 2], "total_tokens": 25}


def test_consecutive_renders_are_byte_identical():
    ws = WorkingSet()
    ws.insert(_make_item(1, source_class=SourceClass.scratch, content="hello"))
    ws.insert(_make_item(2, source_class=SourceClass.system, content="sys"))
    renderer = Renderer(
        working_set=ws,
        policy=Policy(),
        page_index=_empty_page_index(),
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    result_1 = renderer.render(current_turn=0)
    result_2 = renderer.render(current_turn=1)

    assert result_1.messages == result_2.messages
    assert result_1.manifest == result_2.manifest
    assert result_1.total_tokens == result_2.total_tokens


def test_render_does_not_call_page_index_sync_collapse_log():
    page_store = PageStore()
    for item_id in (1, 2, 3):
        page_store.insert(_make_item(item_id, state=ItemState.paged, index_line="x", token_size=1))
    real_page_index = PageIndex(
        page_store=page_store,
        policy=Policy(page_index_budget=1),
        tokenizer=WhitespaceTokenizer(),
        log=MutationLog(),
    )
    spy = _SpyPageIndex(real_page_index)

    ws = WorkingSet()
    ws.insert(_make_item(10, source_class=SourceClass.scratch))
    renderer = Renderer(
        working_set=ws,
        policy=Policy(page_index_budget=1),
        page_index=spy,
        log=MutationLog(),
        is_balanced=lambda: True,
    )

    renderer.render(current_turn=0)

    assert spy.sync_collapse_log_calls == 0
