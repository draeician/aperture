"""Tool-output governance tests.

Step 5 adds only the truncation and dedup governance-helper cases:
all truncation modes, structure handling (plain/lines/json with
fallback), accurate elision counts, no-op below the cap, and dedup
(hashing the final stored form, stub content, and source_class
gating). Full admission wiring is a later step; this file is additive.
"""

from __future__ import annotations

import hashlib
import json

from aperture.governance import dedup_tool_output, register_tool_output_hash, truncate_tool_output
from aperture.items import EventKind, SourceClass, Structure, TruncationMode
from aperture.log import MutationLog
from aperture.tokenizer import WhitespaceTokenizer


TOKENIZER = WhitespaceTokenizer()


# --- Truncation: modes ------------------------------------------------


def test_head_truncation_has_accurate_elision_count():
    log = MutationLog()
    content = "one two three four five six seven eight nine ten"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.plain,
        max_tokens=4,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "one two three four [... 6 tokens elided ...]"
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload == {
        "original_tokens": 10,
        "kept_tokens": 4,
        "mode": TruncationMode.head,
        "structure": Structure.plain,
    }


def test_tail_truncation_has_accurate_elision_count():
    log = MutationLog()
    content = "one two three four five six seven eight nine ten"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.plain,
        max_tokens=4,
        mode=TruncationMode.tail,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "[... 6 tokens elided ...] seven eight nine ten"
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload["original_tokens"] == 10
    assert event.payload["kept_tokens"] == 4


def test_head_tail_truncation_has_accurate_elision_count():
    log = MutationLog()
    content = "one two three four five six seven eight nine ten"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.plain,
        max_tokens=4,
        mode=TruncationMode.head_tail,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "one two [... 6 tokens elided ...] nine ten"
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload["original_tokens"] == 10
    assert event.payload["kept_tokens"] == 4


def test_plain_structure_truncates_by_token_boundaries_only():
    log = MutationLog()
    # Irregular whitespace (double space, tab) must not affect token boundaries.
    content = "alpha  beta\tgamma delta"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.plain,
        max_tokens=2,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "alpha beta [... 2 tokens elided ...]"


def test_no_truncation_when_under_cap():
    log = MutationLog()
    content = "short content here"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.plain,
        max_tokens=10,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == content
    assert log.events(kind=EventKind.truncated) == []


def test_non_tool_output_content_is_not_truncated():
    log = MutationLog()
    content = " ".join(f"word{i}" for i in range(50))

    result = truncate_tool_output(
        source_class=SourceClass.scratch,
        content=content,
        structure=Structure.plain,
        max_tokens=5,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == content
    assert log.events(kind=EventKind.truncated) == []


# --- Truncation: structure = lines --------------------------------------


def test_line_boundary_truncation_cuts_only_at_newlines():
    log = MutationLog()
    content = "line1\nline2\nline3\nline4\nline5"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.lines,
        max_tokens=2,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "line1\nline2\n[... 3 tokens elided ...]"
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload == {
        "original_tokens": 5,
        "kept_tokens": 2,
        "mode": TruncationMode.head,
        "structure": Structure.lines,
    }


# --- Truncation: structure = json ---------------------------------------


def test_json_list_drops_middle_elements_and_inserts_marker_string():
    log = MutationLog()
    content = json.dumps(["alpha", "beta", "gamma", "delta", "epsilon"])

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.json,
        max_tokens=2,
        mode=TruncationMode.head_tail,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert json.loads(result) == ["alpha", "[... 3 tokens elided ...]", "epsilon"]
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload == {
        "original_tokens": 5,
        "kept_tokens": 2,
        "mode": TruncationMode.head_tail,
        "structure": Structure.json,
    }


def test_json_fallback_to_lines_when_parsing_fails():
    log = MutationLog()
    content = "not json at all\nline two here\nline three here\nline four here"

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.json,
        max_tokens=5,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "not json at all\n[... 9 tokens elided ...]"
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload["original_tokens"] == 13
    assert event.payload["kept_tokens"] == 4
    # the declared structure is preserved in the log even though the
    # fallback used lines-style cutting.
    assert event.payload["structure"] == Structure.json


def test_json_fallback_to_lines_when_parsed_value_is_not_a_list():
    log = MutationLog()
    content = json.dumps({"a": 1, "b": 2, "c": 3, "d": 4})

    result = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        structure=Structure.json,
        max_tokens=3,
        mode=TruncationMode.tail,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )

    assert result == "[... 8 tokens elided ...]"
    event = log.events(kind=EventKind.truncated)[0]
    assert event.payload["original_tokens"] == 8
    assert event.payload["kept_tokens"] == 0
    assert event.payload["structure"] == Structure.json


# --- Dedup ---------------------------------------------------------------
#
# dedup_tool_output only ever *reads* hash_registry; it never registers a
# hash itself. Registration happens explicitly, via
# register_tool_output_hash, and is the later admission/facade step's
# responsibility to call only after a submission is successfully
# admitted -- so a governed-but-rejected submission never becomes a
# dedup target.


def test_register_tool_output_hash_computes_sha256_of_final_form():
    registry: dict[str, int] = {}
    content = "final stored content"

    digest = register_tool_output_hash(registry, content, 1)

    expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert digest == expected_hash
    assert registry == {expected_hash: 1}


def test_dedup_lookup_does_not_mutate_registry_on_new_content():
    log = MutationLog()
    registry: dict[str, int] = {}
    content = "final stored content"

    result = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        hash_registry=registry,
        item_id=1,
        log=log,
        turn=0,
    )

    assert result == content
    assert registry == {}
    assert log.events(kind=EventKind.deduped) == []


def test_explicit_registration_required_before_later_dedup():
    log = MutationLog()
    registry: dict[str, int] = {}
    content = "some final content"

    # Before registration, an identical later submission is not a duplicate.
    first_pass = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        hash_registry=registry,
        item_id=1,
        log=log,
        turn=0,
    )
    assert first_pass == content
    assert log.events(kind=EventKind.deduped) == []

    # Only after the caller explicitly registers item 1's stored form...
    register_tool_output_hash(registry, content, 1)

    # ...does a later identical submission get recognized as a duplicate.
    second_pass = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        hash_registry=registry,
        item_id=2,
        log=log,
        turn=1,
    )
    assert second_pass == "[duplicate of item #1]"

    dedup_events = log.events(kind=EventKind.deduped)
    assert len(dedup_events) == 1
    assert dedup_events[0].payload == {"duplicate_item_id": 2, "original_item_id": 1}


def test_rejected_content_is_not_a_dedup_target():
    log = MutationLog()
    registry: dict[str, int] = {}
    content = "content that ends up rejected"

    # item 1 is governed (dedup-checked) but is rejected by the
    # admission check afterward, so the caller never registers it.
    dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        hash_registry=registry,
        item_id=1,
        log=log,
        turn=0,
    )

    # item 2 has byte-identical content but must not be treated as a
    # duplicate of the never-registered, rejected item 1.
    result = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        hash_registry=registry,
        item_id=2,
        log=log,
        turn=1,
    )

    assert result == content
    assert registry == {}
    assert log.events(kind=EventKind.deduped) == []


def test_two_identical_oversized_outputs_dedup_after_truncation():
    log = MutationLog()
    registry: dict[str, int] = {}
    raw = " ".join(f"word{i}" for i in range(20))

    truncated_1 = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=raw,
        structure=Structure.plain,
        max_tokens=5,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=1,
        turn=0,
    )
    final_1 = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=truncated_1,
        hash_registry=registry,
        item_id=1,
        log=log,
        turn=0,
    )
    assert final_1 == truncated_1
    # item 1 is admitted successfully -> the facade registers its final form.
    register_tool_output_hash(registry, final_1, 1)

    truncated_2 = truncate_tool_output(
        source_class=SourceClass.tool_output,
        content=raw,
        structure=Structure.plain,
        max_tokens=5,
        mode=TruncationMode.head,
        tokenizer=TOKENIZER,
        log=log,
        item_id=2,
        turn=1,
    )
    assert truncated_2 == truncated_1

    final_2 = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=truncated_2,
        hash_registry=registry,
        item_id=2,
        log=log,
        turn=1,
    )
    assert final_2 == "[duplicate of item #1]"

    dedup_events = log.events(kind=EventKind.deduped)
    assert len(dedup_events) == 1
    assert dedup_events[0].payload == {"duplicate_item_id": 2, "original_item_id": 1}


def test_dedup_stub_references_correct_original_id():
    log = MutationLog()
    registry: dict[str, int] = {}
    content = "some final content"

    register_tool_output_hash(registry, content, 42)
    result = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content,
        hash_registry=registry,
        item_id=99,
        log=log,
        turn=1,
    )

    assert result == "[duplicate of item #42]"


def test_dedup_matches_prior_working_and_paged_items():
    log = MutationLog()
    registry: dict[str, int] = {}
    content_working = "content from a currently-working item"
    content_paged = "content from a currently-paged item"
    register_tool_output_hash(registry, content_working, 10)
    register_tool_output_hash(registry, content_paged, 20)

    result_working = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content_working,
        hash_registry=registry,
        item_id=11,
        log=log,
        turn=0,
    )
    assert result_working == "[duplicate of item #10]"

    result_paged = dedup_tool_output(
        source_class=SourceClass.tool_output,
        content=content_paged,
        hash_registry=registry,
        item_id=21,
        log=log,
        turn=1,
    )
    assert result_paged == "[duplicate of item #20]"


def test_non_tool_output_content_is_not_deduped():
    log = MutationLog()
    content = "identical content"
    # Even if a colliding hash is already registered, non-tool_output
    # source classes must short-circuit before any registry lookup.
    registry = {hashlib.sha256(content.encode("utf-8")).hexdigest(): 1}

    result = dedup_tool_output(
        source_class=SourceClass.scratch,
        content=content,
        hash_registry=registry,
        item_id=2,
        log=log,
        turn=0,
    )

    assert result == content
    assert log.events(kind=EventKind.deduped) == []
