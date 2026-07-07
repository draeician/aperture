"""MNEME opacity tests (Step 9 facade/integration).

Full coverage per IMPLEMENTATION_ORDER.md Step 9: arbitrary mneme_meta
round-trips byte-identical through page/recall/export; behavior
differs only on the render_restricted key; Aperture never reads from
or writes to MNEME (no MNEME client exists anywhere in this codebase;
mneme_meta passes through completely unexamined beyond that one key).
"""

from __future__ import annotations

from aperture.items import Provenance, SourceClass, Submission
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


class _Opaque:
    """A marker object Aperture must never introspect or call methods on."""


def test_arbitrary_mneme_meta_round_trips_byte_identical_through_page_recall_export():
    original_meta = {
        "render_restricted": False,
        "custom_key": "custom_value",
        "nested": {"a": 1, "b": [1, 2, 3]},
        "mneme_record_ref": "xyz123",
    }
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    result_a = kernel.submit(
        _submission(content=_A_CONTENT, ttl_turns=1, priority=5, mneme_meta=dict(original_meta))
    )
    kernel.submit(_submission(content=_B_CONTENT, priority=0))

    # working
    assert kernel._working_set.get(result_a.item_id).mneme_meta == original_meta

    # working -> paged
    kernel.next_turn()
    kernel.balance()
    assert kernel._page_store.get(result_a.item_id).mneme_meta == original_meta

    # paged -> working (recall)
    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)
    assert kernel._working_set.get(result_a.item_id).mneme_meta == original_meta

    # export
    export = kernel.end_session()
    exported_item = next(item for item in export.working_set if item["id"] == result_a.item_id)
    assert exported_item["mneme_meta"] == original_meta


def test_behavior_differs_only_on_render_restricted():
    policy = Policy()
    kernel = Aperture(policy)

    restricted = kernel.submit(
        _submission(
            content="restricted content",
            mneme_meta={"render_restricted": True, "some_other_flag": True},
        )
    )
    not_restricted_false = kernel.submit(
        _submission(content="visible false", mneme_meta={"render_restricted": False})
    )
    not_restricted_none = kernel.submit(_submission(content="visible none", mneme_meta=None))
    not_restricted_missing_key = kernel.submit(
        _submission(content="visible empty dict", mneme_meta={})
    )

    result = kernel.render()

    assert restricted.item_id not in result.manifest
    assert not_restricted_false.item_id in result.manifest
    assert not_restricted_none.item_id in result.manifest
    assert not_restricted_missing_key.item_id in result.manifest

    # the restricted item remains in the WorkingSet, untouched, just
    # excluded from render output/manifest/totals.
    assert restricted.item_id in kernel._working_set
    assert all("restricted content" not in msg["content"] for msg in result.messages)


def test_aperture_never_reads_or_writes_mneme():
    marker = _Opaque()
    kernel = Aperture(Policy())

    result = kernel.submit(
        _submission(
            content="hello",
            mneme_meta={"render_restricted": False, "opaque_ref": marker},
        )
    )

    # the opaque value passes through completely untouched (same object
    # reference): Aperture never introspects, calls, or copies it -- it
    # only ever reads the single render_restricted key.
    item = kernel._working_set.get(result.item_id)
    assert item.mneme_meta["opaque_ref"] is marker
