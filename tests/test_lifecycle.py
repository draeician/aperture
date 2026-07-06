"""Lifecycle tests.

Step 1 adds only policy construction-time validation cases. Step 2 adds
tokenizer construction/determinism cases. Full state-machine lifecycle
tests are added in a later implementation step; this file is additive.
"""

from __future__ import annotations

import pytest

from aperture.errors import PolicyError
from aperture.items import SourceClass
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
