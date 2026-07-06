"""Redaction tests.

Step 5 adds only the redaction governance-helper cases: rule ordering,
marker format, content/index_line redaction, no-log-on-no-match, and
log payload shape. Tool-output truncation/dedup live in
tests/test_tool_output.py. Full admission wiring is a later step; this
file is additive.
"""

from __future__ import annotations

from aperture.governance import redact
from aperture.items import EventKind
from aperture.log import MutationLog
from aperture.policy import RedactionRule


def test_rules_apply_in_policy_order():
    log = MutationLog()
    rules = (
        RedactionRule(rule_id="digits", pattern=r"\d+", label="NUM"),
        RedactionRule(rule_id="brackets", pattern=r"\[REDACTED:NUM\]", label="WAS_NUM"),
    )

    content, _ = redact(
        content="value 123", index_line=None, rules=rules, log=log, item_id=1, turn=0
    )

    assert content == "value [REDACTED:WAS_NUM]"


def test_marker_format():
    log = MutationLog()
    rules = (RedactionRule(rule_id="secret", pattern="secret", label="SECRET"),)

    content, _ = redact(
        content="my secret value", index_line=None, rules=rules, log=log, item_id=1, turn=0
    )

    assert content == "my [REDACTED:SECRET] value"


def test_content_is_redacted():
    log = MutationLog()
    rules = (RedactionRule(rule_id="email", pattern=r"\S+@\S+", label="EMAIL"),)

    content, index_line = redact(
        content="contact me at a@b.com please",
        index_line="no email here",
        rules=rules,
        log=log,
        item_id=1,
        turn=0,
    )

    assert content == "contact me at [REDACTED:EMAIL] please"
    assert index_line == "no email here"


def test_index_line_is_redacted():
    log = MutationLog()
    rules = (RedactionRule(rule_id="email", pattern=r"\S+@\S+", label="EMAIL"),)

    content, index_line = redact(
        content="no match here",
        index_line="contact a@b.com now",
        rules=rules,
        log=log,
        item_id=1,
        turn=0,
    )

    assert content == "no match here"
    assert index_line == "contact [REDACTED:EMAIL] now"


def test_no_log_when_no_match():
    log = MutationLog()
    rules = (RedactionRule(rule_id="email", pattern=r"\S+@\S+", label="EMAIL"),)

    redact(
        content="nothing to see",
        index_line="still nothing",
        rules=rules,
        log=log,
        item_id=1,
        turn=0,
    )

    assert log.events(kind=EventKind.redacted) == []


def test_log_payload_never_includes_matched_text():
    log = MutationLog()
    rules = (RedactionRule(rule_id="email", pattern=r"\S+@\S+", label="EMAIL"),)

    redact(
        content="contact a@b.com",
        index_line=None,
        rules=rules,
        log=log,
        item_id=1,
        turn=0,
    )

    events = log.events(kind=EventKind.redacted)
    assert len(events) == 1
    payload = events[0].payload
    assert set(payload.keys()) == {"rule_id", "match_count"}
    assert "a@b.com" not in str(payload)


def test_multiple_rules_log_correct_match_counts():
    log = MutationLog()
    rules = (
        RedactionRule(rule_id="digits", pattern=r"\d+", label="NUM"),
        RedactionRule(rule_id="vowel_a", pattern="a", label="A"),
    )

    redact(
        content="a1 b2 c3",
        index_line="banana",
        rules=rules,
        log=log,
        item_id=7,
        turn=2,
    )

    events = log.events(kind=EventKind.redacted, item_id=7)
    assert len(events) == 2

    digits_event = next(e for e in events if e.payload["rule_id"] == "digits")
    assert digits_event.payload["match_count"] == 3  # "1", "2", "3" in content; none in index_line

    a_event = next(e for e in events if e.payload["rule_id"] == "vowel_a")
    # content after digit redaction: "a[REDACTED:NUM] b[REDACTED:NUM] c[REDACTED:NUM]" -> one lowercase 'a'
    # index_line "banana" -> three lowercase 'a's
    assert a_event.payload["match_count"] == 4
