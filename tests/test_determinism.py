"""Determinism tests (Step 11).

The core contract: for the same seed, run_fuzz_session (tests/conftest.py)
builds the same policy, generates the same operation sequence, and
driving that sequence against a fresh Aperture instance always produces
the same plain-data outcome record -- covering exported logs, eviction
sequences (via balance()/recall() BalanceReports), render bytes (via
render() RenderResults and the page index text they embed), and explain
output, all in one comparable structure. Any divergence across replays
is an implementation bug, not a flake.
"""

from __future__ import annotations

from conftest import run_fuzz_session

SEED_COUNT = 50
REPLAYS = 3


def test_fifty_sessions_replayed_three_times_are_deterministic():
    for seed in range(SEED_COUNT):
        results = [run_fuzz_session(seed) for _ in range(REPLAYS)]
        first = results[0]
        for replay_index, replay in enumerate(results[1:], start=2):
            assert replay == first, (
                f"seed {seed} replay {replay_index} diverged from replay 1 "
                "-- nondeterminism is an implementation bug"
            )


def test_different_seeds_are_not_trivially_identical():
    # Sanity check on the generator itself: seeds must actually vary the
    # policy/operations, or the determinism test above would be vacuous.
    outcomes = {seed: run_fuzz_session(seed) for seed in (0, 1, 2, 3, 4)}
    distinct = {repr(sorted(o.items())) for o in outcomes.values()}
    assert len(distinct) > 1
