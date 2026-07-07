"""Lifecycle tests.

Step 1 adds only policy construction-time validation cases. Step 2 adds
tokenizer construction/determinism cases. Step 9 adds the full facade
state-machine walk, admission-pipeline id/rejection semantics,
end_session/SessionClosedError, instance isolation, and the
no-sockets/no-file-writes check. This file is additive.
"""

from __future__ import annotations

import builtins
import socket

import pytest

from aperture.errors import InvalidItemError, PolicyError, SessionClosedError
from aperture.items import EventKind, ItemState, Provenance, SourceClass, Submission
from aperture.kernel import Aperture
from aperture.policy import Policy, RedactionRule
import aperture.tokenizer as tokenizer_module
from aperture.tokenizer import WhitespaceTokenizer, get_tokenizer


# --- Step 1: Policy construction ---------------------------------------


def test_valid_defaults_construct():
    policy = Policy()
    assert policy.budget_total > 0
    assert policy.render_order[0] == SourceClass.system
    assert SourceClass.page_index in policy.render_order
    assert SourceClass.system not in policy.class_eviction_order
    assert policy.tokenizer_id == "whitespace"


def test_budget_total_must_be_positive(policy_factory):
    with pytest.raises(PolicyError):
        policy_factory(budget_total=0)
    with pytest.raises(PolicyError):
        policy_factory(budget_total=-1)


@pytest.mark.parametrize("fraction", [0, -0.5, 1.5])
def test_subbudget_fraction_out_of_range_raises(policy_factory, fraction):
    with pytest.raises(PolicyError):
        policy_factory(class_subbudgets={SourceClass.tool_output: fraction})


def test_subbudget_fraction_at_upper_bound_is_valid(policy_factory):
    policy_factory(class_subbudgets={SourceClass.tool_output: 1.0})


def test_system_in_class_eviction_order_raises(policy_factory):
    with pytest.raises(PolicyError):
        policy_factory(class_eviction_order=(SourceClass.system, SourceClass.scratch))


def test_render_order_must_start_with_system(policy_factory):
    with pytest.raises(PolicyError):
        policy_factory(
            render_order=(SourceClass.user_fact, SourceClass.system, SourceClass.page_index)
        )


def test_render_order_must_include_page_index(policy_factory):
    with pytest.raises(PolicyError):
        policy_factory(render_order=(SourceClass.system, SourceClass.user_fact))


def test_invalid_redaction_pattern_raises(policy_factory):
    with pytest.raises(PolicyError):
        policy_factory(
            redaction_rules=(RedactionRule(rule_id="bad", pattern="[unclosed", label="X"),)
        )


def test_valid_redaction_pattern_constructs(policy_factory):
    policy_factory(
        redaction_rules=(RedactionRule(rule_id="ok", pattern=r"\d+", label="NUM"),)
    )


@pytest.mark.parametrize("tokenizer_id", ["bogus", "tiktoken:", "TIKTOKEN:cl100k_base"])
def test_invalid_tokenizer_id_format_raises(policy_factory, tokenizer_id):
    with pytest.raises(PolicyError):
        policy_factory(tokenizer_id=tokenizer_id)


def test_valid_tiktoken_tokenizer_id_constructs(policy_factory):
    policy_factory(tokenizer_id="tiktoken:cl100k_base")


# --- Step 2: Tokenizer ---------------------------------------------------


def test_whitespace_tokenizer_count_is_deterministic():
    tok = WhitespaceTokenizer()
    text = "the quick brown fox jumps"
    assert tok.count(text) == 5
    assert tok.count(text) == tok.count(text)


def test_whitespace_tokenizer_split_matches_count():
    tok = WhitespaceTokenizer()
    text = "one two three"
    parts = tok.split(text)
    assert parts == ["one", "two", "three"]
    assert tok.count(text) == len(parts)


def test_whitespace_tokenizer_handles_empty_text():
    tok = WhitespaceTokenizer()
    assert tok.count("") == 0
    assert tok.split("") == []


def test_get_tokenizer_whitespace_is_default_core_path():
    tok = get_tokenizer("whitespace")
    assert isinstance(tok, WhitespaceTokenizer)


def test_get_tokenizer_unrecognized_id_raises(policy_factory):
    with pytest.raises(PolicyError):
        get_tokenizer("not-a-real-tokenizer")


def test_tiktoken_unavailable_raises_policy_error_naming_extra(monkeypatch):
    monkeypatch.setattr(tokenizer_module, "tiktoken", None)
    with pytest.raises(PolicyError, match="tokens"):
        get_tokenizer("tiktoken:cl100k_base")


def test_tiktoken_adapter_when_installed():
    tiktoken = pytest.importorskip("tiktoken")
    tok = get_tokenizer("tiktoken:cl100k_base")
    text = "hello world"
    assert tok.count(text) == len(tiktoken.get_encoding("cl100k_base").encode(text))
    assert tok.split(text) == [
        tiktoken.get_encoding("cl100k_base").decode([t])
        for t in tiktoken.get_encoding("cl100k_base").encode(text)
    ]


# --- Step 9: Facade lifecycle ---------------------------------------------

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


def test_full_state_machine_walk():
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    kernel.submit(_submission(content=_B_CONTENT, priority=0))

    # working
    item = kernel._working_set.get(result_a.item_id)
    assert item.state == ItemState.working

    # working -> paged (TTL eviction under budget pressure)
    kernel.next_turn()
    kernel.balance()
    item = kernel._page_store.get(result_a.item_id)
    assert item.state == ItemState.paged
    assert result_a.item_id not in kernel._working_set

    # paged -> working (recall)
    kernel.extend_ttl(result_a.item_id, turns=1000)
    kernel.recall(result_a.item_id)
    item = kernel._working_set.get(result_a.item_id)
    assert item.state == ItemState.working
    assert result_a.item_id not in kernel._page_store


def test_invalid_submission_fails_before_id_assignment_and_logs_nothing():
    kernel = Aperture(Policy())

    for bad in (
        _submission(source_class=SourceClass.page_index, content="x"),
        _submission(content=""),
        _submission(content="x", ttl_turns=-1),
    ):
        with pytest.raises(InvalidItemError):
            kernel.submit(bad)

    assert kernel._log.export() == []

    # the id counter was never advanced by the failed attempts
    result = kernel.submit(_submission(content="first real submission"))
    assert result.item_id == 1


def test_rejected_submission_consumes_id_and_creates_explainable_rejected_event():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    oversized = " ".join(f"word{i}" for i in range(50))  # 50 tok > contested_pool(10)
    result = kernel.submit(_submission(content=oversized))

    assert result.accepted is False
    assert result.item_id == 1
    assert result.item_id not in kernel._working_set
    assert result.item_id not in kernel._page_store

    events = kernel._log.events(kind=EventKind.rejected, item_id=1)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["source_class"] == SourceClass.scratch
    assert payload["token_size"] == 50
    assert payload["turn"] == 0
    assert "reason" in payload


def test_id_monotonicity_with_rejection_gaps():
    policy = Policy(budget_total=10, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    oversized = " ".join(f"word{i}" for i in range(50))
    r1 = kernel.submit(_submission(content="ok"))  # accepted -> id 1
    r2 = kernel.submit(_submission(content=oversized))  # rejected -> id 2
    r3 = kernel.submit(_submission(content=oversized))  # rejected -> id 3
    r4 = kernel.submit(_submission(content="ok too"))  # accepted -> id 4

    assert (r1.item_id, r2.item_id, r3.item_id, r4.item_id) == (1, 2, 3, 4)
    assert r1.accepted and r4.accepted
    assert not r2.accepted and not r3.accepted
    assert list(kernel._working_set) and {i.id for i in kernel._working_set} == {1, 4}


def test_end_session_export_completeness():
    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)

    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    result_b = kernel.submit(_submission(content=_B_CONTENT, priority=0))
    kernel.next_turn()
    kernel.balance()  # A paged, B working

    log_len_before_export = len(kernel._log.export())
    export = kernel.end_session()

    assert [item["id"] for item in export.working_set] == [result_b.item_id]
    assert [item["id"] for item in export.page_store] == [result_a.item_id]
    assert export.working_set[0]["content"] == _B_CONTENT
    assert export.page_store[0]["content"] == _A_CONTENT
    # the log export includes everything logged so far, plus session_export itself
    assert len(export.log) == log_len_before_export + 1
    assert export.log[-1]["kind"] == EventKind.session_export


def test_post_close_calls_raise_session_closed_error():
    kernel = Aperture(Policy())
    result = kernel.submit(_submission(content="hello"))
    kernel.end_session()

    with pytest.raises(SessionClosedError):
        kernel.submit(_submission(content="too late"))
    with pytest.raises(SessionClosedError):
        kernel.balance()
    with pytest.raises(SessionClosedError):
        kernel.render()
    with pytest.raises(SessionClosedError):
        kernel.recall(result.item_id)
    with pytest.raises(SessionClosedError):
        kernel.pin(result.item_id, True)
    with pytest.raises(SessionClosedError):
        kernel.extend_ttl(result.item_id, 1)
    with pytest.raises(SessionClosedError):
        kernel.next_turn()
    with pytest.raises(SessionClosedError):
        kernel.recall_request_schema()
    with pytest.raises(SessionClosedError):
        kernel.handle_recall_request({"item_id": result.item_id})
    with pytest.raises(SessionClosedError):
        kernel.end_session()


def test_two_aperture_instances_share_no_state():
    kernel_1 = Aperture(Policy())
    kernel_2 = Aperture(Policy())

    result_1 = kernel_1.submit(_submission(content="only in kernel 1"))
    # kernel_2's id counter is independent -- it also starts at 1, not 2.
    result_2 = kernel_2.submit(_submission(content="only in kernel 2"))
    assert result_1.item_id == 1
    assert result_2.item_id == 1

    assert kernel_1._working_set.get(1).content == "only in kernel 1"
    assert kernel_2._working_set.get(1).content == "only in kernel 2"
    assert len(kernel_1._log.export()) == 1
    assert len(kernel_2._log.export()) == 1
    assert kernel_1._working_set is not kernel_2._working_set
    assert kernel_1._log is not kernel_2._log
    assert kernel_1._page_store is not kernel_2._page_store
    assert kernel_1._tool_output_hashes is not kernel_2._tool_output_hashes


def test_representative_session_opens_no_sockets_and_writes_no_files(monkeypatch):
    original_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"unexpected file write attempted: {file!r} mode={mode!r}")
        return original_open(file, mode, *args, **kwargs)

    def guarded_socket(*args, **kwargs):
        raise AssertionError("unexpected socket creation attempted")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(socket, "socket", guarded_socket)

    policy = Policy(budget_total=130, reply_headroom=0, token_safety_margin=0.0)
    kernel = Aperture(policy)
    result_a = kernel.submit(_submission(content=_A_CONTENT, ttl_turns=1, priority=5))
    kernel.submit(_submission(content=_B_CONTENT, priority=0))
    kernel.next_turn()
    kernel.balance()
    kernel.render()
    kernel.pin(result_a.item_id, True)
    kernel.pin(result_a.item_id, False)
    kernel.extend_ttl(result_a.item_id, 100)
    kernel.next_turn()
    kernel.balance()
    kernel.end_session()


def test_fuzz_session_opens_no_sockets_and_writes_no_files(monkeypatch):
    # Step 11: same guard as the representative-session check above, but
    # driving actual generated fuzz sessions (tests/conftest.py), per
    # PHASE0_BRIEF.md's test_lifecycle.py bullet ("a fuzz session opens no
    # sockets and writes no files"). Kept small and non-duplicative: the
    # guard logic itself is identical to the test above.
    from conftest import run_fuzz_session

    original_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in ("w", "a", "x", "+")):
            raise AssertionError(f"unexpected file write attempted: {file!r} mode={mode!r}")
        return original_open(file, mode, *args, **kwargs)

    def guarded_socket(*args, **kwargs):
        raise AssertionError("unexpected socket creation attempted")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(socket, "socket", guarded_socket)

    for seed in range(5):
        run_fuzz_session(seed)
