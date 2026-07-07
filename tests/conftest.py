"""Shared fixtures: policy and submission factories, and the Step 11
seeded fuzz session generator/runner.

The fuzz generator/runner are plain module-level functions (not
fixtures) so tests/test_determinism.py can call run_fuzz_session(seed)
directly, any number of times, for any seed.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from aperture.errors import ApertureError
from aperture.items import Provenance, SourceClass, Structure, Submission
from aperture.kernel import Aperture
from aperture.policy import Policy, RedactionRule


@pytest.fixture
def policy_factory():
    def _make(**overrides):
        return Policy(**overrides)

    return _make


@pytest.fixture
def default_policy(policy_factory):
    return policy_factory()


@pytest.fixture
def submission_factory():
    def _make(source_class=SourceClass.scratch, content="hello", provenance=None, **overrides):
        if provenance is None:
            provenance = Provenance(submitted_by="test")
        return Submission(
            source_class=source_class,
            content=content,
            provenance=provenance,
            **overrides,
        )

    return _make


# --- Step 11: seeded fuzz session generator/runner ------------------------
#
# Deterministic by construction: every random choice below is drawn from a
# random.Random(seed) instance created fresh per run (never the global
# random module), so the same seed always yields the same policy and
# operation sequence, and driving that sequence against a fresh Aperture
# instance always yields the same outcome. No wall clock, repr/object
# identity, unordered dict/set iteration, temp paths, or environment
# values are used anywhere in generation or comparison.

_SOURCE_CLASSES = (
    SourceClass.system,
    SourceClass.user_fact,
    SourceClass.conversation,
    SourceClass.tool_output,
    SourceClass.scratch,
    SourceClass.mneme_import,
)

_BOGUS_ITEM_ID = 999_999  # guaranteed never assigned within a fuzz session


def _build_fuzz_policy(rng: random.Random) -> Policy:
    return Policy(
        budget_total=rng.choice([60, 100, 150, 300]),
        reply_headroom=rng.choice([0, 5]),
        page_index_budget=rng.choice([15, 30, 100]),
        class_subbudgets={SourceClass.scratch: 0.5} if rng.random() < 0.5 else {},
        tool_output_max_tokens=rng.choice([8, 15, 40]),
        redaction_rules=(
            RedactionRule(rule_id="digits", pattern=r"\d{3,}", label="NUM"),
            RedactionRule(rule_id="email", pattern=r"[\w.]+@[\w.]+", label="EMAIL"),
        ),
        token_safety_margin=rng.choice([0.0, 0.1]),
    )


def _random_content(rng: random.Random, *, redaction_hit: bool, long: bool) -> str:
    words = [f"tok{rng.randint(0, 9)}" for _ in range(rng.randint(3, 6))]
    if long:
        words += [f"filler{i}" for i in range(rng.randint(20, 40))]
    if redaction_hit:
        words.append(rng.choice(["123456", "someone@example.com"]))
    return " ".join(words)


def generate_fuzz_operations(rng: random.Random, num_ops: int = 40) -> list[dict]:
    """Deterministically generates a plain-data operation sequence (no
    live kernel involved) driven entirely by rng. known_ids simulates id
    assignment (every "submit" op consumes the next sequential id,
    matching real Admission Pipeline id-assignment order; "submit_invalid"
    ops never consume an id, since invalid submissions fail before id
    assignment).
    """
    ops: list[dict] = []
    known_ids: list[int] = []
    tool_output_pool: list[str] = []
    next_id = 1

    op_choices = (
        "submit_valid",
        "submit_invalid",
        "next_turn",
        "balance",
        "render",
        "recall_known",
        "recall_unknown",
        "extend_ttl_known",
        "extend_ttl_invalid_turns",
        "pin_toggle",
        "handle_recall_request",
        "explain_budget",
        "explain_item",
        "explain_absence",
        "explain_starved",
        "explain_render_last",
        "explain_log",
    )
    needs_known_id = {
        "recall_known",
        "extend_ttl_known",
        "pin_toggle",
        "explain_item",
        "explain_absence",
    }

    for _ in range(num_ops):
        op_name = rng.choice(op_choices)
        if op_name in needs_known_id and not known_ids:
            op_name = "submit_valid"

        if op_name == "submit_valid":
            source_class = rng.choice(_SOURCE_CLASSES)
            is_tool_output = source_class == SourceClass.tool_output
            if is_tool_output and tool_output_pool and rng.random() < 0.3:
                content = rng.choice(tool_output_pool)  # forces a dedup hit
            else:
                content = _random_content(
                    rng,
                    redaction_hit=rng.random() < 0.3,
                    long=is_tool_output and rng.random() < 0.5,
                )
                if is_tool_output:
                    tool_output_pool.append(content)
            ops.append(
                {
                    "op": "submit",
                    "source_class": source_class,
                    "content": content,
                    "pinned": rng.random() < 0.2,
                    "ttl_turns": rng.choice([None, 1, 2, 5]),
                    "restricted": rng.random() < 0.15,
                    "structure": (
                        Structure.lines if is_tool_output and rng.random() < 0.3 else Structure.plain
                    ),
                }
            )
            known_ids.append(next_id)
            next_id += 1
        elif op_name == "submit_invalid":
            ops.append({"op": "submit_invalid", "kind": rng.choice(["empty", "negative_ttl", "reserved_class"])})
        elif op_name == "next_turn":
            ops.append({"op": "next_turn"})
        elif op_name == "balance":
            ops.append({"op": "balance"})
        elif op_name == "render":
            ops.append({"op": "render"})
        elif op_name == "recall_known":
            ops.append({"op": "recall", "id_ref": rng.choice(known_ids)})
        elif op_name == "recall_unknown":
            ops.append({"op": "recall", "id_ref": _BOGUS_ITEM_ID})
        elif op_name == "extend_ttl_known":
            ops.append(
                {"op": "extend_ttl", "id_ref": rng.choice(known_ids), "turns": rng.choice([1, 2, 5, 10])}
            )
        elif op_name == "extend_ttl_invalid_turns":
            ref = rng.choice(known_ids) if known_ids else _BOGUS_ITEM_ID
            ops.append({"op": "extend_ttl", "id_ref": ref, "turns": rng.choice([0, -1, -5])})
        elif op_name == "pin_toggle":
            ops.append({"op": "pin", "id_ref": rng.choice(known_ids), "pinned": rng.random() < 0.5})
        elif op_name == "handle_recall_request":
            if known_ids and rng.random() < 0.7:
                payload = {"item_id": rng.choice(known_ids)}
            else:
                payload = rng.choice([{}, {"item_id": "not-an-int"}, {"item_id": _BOGUS_ITEM_ID}])
            ops.append({"op": "handle_recall_request", "payload": payload})
        elif op_name == "explain_budget":
            ops.append({"op": "explain_budget"})
        elif op_name == "explain_item":
            ops.append({"op": "explain_item", "id_ref": rng.choice(known_ids)})
        elif op_name == "explain_absence":
            ops.append({"op": "explain_absence", "id_ref": rng.choice(known_ids)})
        elif op_name == "explain_starved":
            ops.append({"op": "explain_starved"})
        elif op_name == "explain_render_last":
            ops.append({"op": "explain_render_last"})
        elif op_name == "explain_log":
            ops.append({"op": "explain_log"})

    return ops


def _to_plain(value):
    """Converts frozen-dataclass facade results (AdmissionResult,
    BalanceReport, RenderResult, SessionExport) into plain dicts for
    comparison; passes already-plain data (dict/list/int/str/None,
    explain.*'s plain dicts) through unchanged.
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return value


def _call(fn):
    """Invokes fn, recording either its (plain) result or -- if it raises
    a Phase 0 ApertureError, as the fuzzed sequence sometimes intends
    (e.g. render() while unbalanced, recall() on an expired/unknown id)
    -- the error type and any known payload fields, deterministically.
    Anything other than ApertureError propagates (a real bug, not a
    recorded outcome).
    """
    try:
        result = fn()
    except ApertureError as exc:
        payload = {}
        for attr in ("pinned_total", "ceiling", "item_id", "expiry_turn"):
            if hasattr(exc, attr):
                payload[attr] = getattr(exc, attr)
        return {"ok": False, "error_type": type(exc).__name__, "error_payload": payload}
    return {"ok": True, "result": _to_plain(result)}


def _execute_operation(kernel: Aperture, op: dict) -> dict:
    name = op["op"]

    if name == "submit":
        mneme_meta = {"render_restricted": True} if op["restricted"] else None
        submission = Submission(
            source_class=op["source_class"],
            content=op["content"],
            provenance=Provenance(submitted_by="fuzz"),
            pinned=op["pinned"],
            ttl_turns=op["ttl_turns"],
            mneme_meta=mneme_meta,
            structure=op["structure"],
        )
        return _call(lambda: kernel.submit(submission))

    if name == "submit_invalid":
        if op["kind"] == "empty":
            submission = Submission(
                source_class=SourceClass.scratch, content="", provenance=Provenance(submitted_by="fuzz")
            )
        elif op["kind"] == "negative_ttl":
            submission = Submission(
                source_class=SourceClass.scratch,
                content="x",
                provenance=Provenance(submitted_by="fuzz"),
                ttl_turns=-1,
            )
        else:
            submission = Submission(
                source_class=SourceClass.page_index,
                content="x",
                provenance=Provenance(submitted_by="fuzz"),
            )
        return _call(lambda: kernel.submit(submission))

    if name == "next_turn":
        return _call(kernel.next_turn)

    if name == "balance":
        return _call(kernel.balance)

    if name == "render":
        return _call(kernel.render)

    if name == "recall":
        return _call(lambda: kernel.recall(op["id_ref"]))

    if name == "extend_ttl":
        return _call(lambda: kernel.extend_ttl(op["id_ref"], op["turns"]))

    if name == "pin":
        return _call(lambda: kernel.pin(op["id_ref"], op["pinned"]))

    if name == "handle_recall_request":
        return _call(lambda: kernel.handle_recall_request(op["payload"]))

    if name == "explain_budget":
        return _call(kernel.explain.budget)

    if name == "explain_item":
        return _call(lambda: kernel.explain.item(op["id_ref"]))

    if name == "explain_absence":
        return _call(lambda: kernel.explain.absence(op["id_ref"]))

    if name == "explain_starved":
        return _call(kernel.explain.starved)

    if name == "explain_render_last":
        return _call(lambda: kernel.explain.render("last"))

    if name == "explain_log":
        return _call(kernel.explain.log)

    raise AssertionError(f"unknown fuzz op: {name!r}")  # pragma: no cover


def run_fuzz_session(seed: int, num_ops: int = 40) -> dict:
    """Builds a policy and operation sequence from seed, drives a fresh
    Aperture instance through the actual facade (submit/balance/render/
    recall/pin/extend_ttl/handle_recall_request/explain.*), then ends the
    session. Returns a plain-data outcome record: comparing two calls
    with the same seed via == is the whole determinism contract.
    """
    rng = random.Random(seed)
    policy = _build_fuzz_policy(rng)
    ops = generate_fuzz_operations(rng, num_ops=num_ops)

    kernel = Aperture(policy)
    outcomes = [_execute_operation(kernel, op) for op in ops]
    export = kernel.end_session()

    return {
        "policy": _to_plain(policy),
        "outcomes": outcomes,
        "export": _to_plain(export),
    }
