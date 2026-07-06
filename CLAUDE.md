# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Aperture is a deterministic, in-process Python 3.11+ context governor for LLM applications. It decides which structured context items enter the next model call under an explicit token budget and policy, evicts non-destructively (paged items go to a session-scoped store, never deleted), maintains a recallable page index, redacts by pattern at admission, and can explain any inclusion/exclusion via an append-only mutation log. It is stdlib-only at the core (`tiktoken` is an optional extra for token counting); zero model calls, zero network, zero persistence beyond the process.

Aperture is explicitly **not**: durable memory, RAG (no search/rank/embed/fetch), an agent framework (no tool registration/loops/provider SDKs), or intelligent (priority is always declared, never inferred from content). See the "MNEME Boundary" in README.md: MNEME owns durable governed memory; Aperture only budgets what the host hands it for one render.

## Repository State

This repo currently contains only planning/spec documents — no `aperture/` package or `pyproject.toml` exists yet. Implementation has not started. The stray root-level `errors` file is leftover scratch, not the real `aperture/errors.py` called for in Step 1 of the implementation order.

## Authoritative Documents — Read Before Writing Code

- **`PHASE0_BRIEF.md`** is the authoritative specification for all Phase 0 behavior (data types, policy model, admission pipeline, eviction algorithm, page index rules, recall rules, tool-output governance, redaction rules, render contract, explain contract, error types, full test suite description, acceptance criteria). Do not implement behavior not specified there.
- **`AGENTS.md`** is the repository rulebook. If it conflicts with `PHASE0_BRIEF.md`, stop and ask for clarification rather than inventing architecture.
- **`IMPLEMENTATION_ORDER.md`** defines the exact, mandatory build sequence (12 steps, foundations → tokenizer → mutation log → containers → governance pipeline → budget governor → page index → renderer → facade → explain API → determinism/fuzz → acceptance sweep). Each step names the only files to create and tests to write for that step; do not create files or write tests ahead of the current step, and do not advance until all tests written so far pass.

## Hard Prohibitions (from AGENTS.md)

Never add: inference, summarization, relevance scoring, embeddings, retrieval, search, durable memory, database persistence, file-backed state, networking, model calls, async, threads, framework/provider-SDK adapters, MNEME clients, replay/WAL semantics, or policy DSLs. The core must remain stdlib-only; `tiktoken` may only ever be an optional extra.

## Determinism Requirement

Same inputs + same policy must always produce byte-identical logs, eviction sequences, page index output, render output, and explain output. Any nondeterminism found is a correctness bug, not a flake — lock fuzz-test seeds, never paper over.

## Testing (once implemented, per PHASE0_BRIEF.md)

- Test runner is `pytest`, with stdlib `random` and fixed seeds for fuzz tests.
- Run the full suite: `pytest`
- Run one file: `pytest tests/test_eviction.py`
- Run one test: `pytest tests/test_eviction.py::test_name`
- Tests define correctness: never weaken a test, delete an invariant, or replace deterministic behavior with heuristic behavior to make something pass.

## Architecture (target layout per PHASE0_BRIEF.md)

```
aperture/
    __init__.py       # public API surface only
    items.py          # ContextItem, enums, states
    policy.py         # Policy and sub-configs, validation
    working_set.py    # WorkingSet
    page_store.py     # PageStore
    page_index.py     # PageIndex
    budget.py         # BudgetGovernor (balance/eviction)
    governance.py     # redaction, tool-output truncation, dedup (admission pipeline)
    renderer.py        # Renderer
    log.py            # MutationLog, event types
    explain.py         # Explain/Why API (reads MutationLog + current state)
    tokenizer.py       # tokenizer protocol + whitespace default + optional tiktoken adapter
    errors.py          # error types
    kernel.py          # Aperture facade wiring the above
tests/
    conftest.py        # fixtures: policies, item factories, seeded fuzz session generator
    test_*.py          # one file per concern, see PHASE0_BRIEF.md Test Suite section
```

Key architectural facts that span multiple files, worth internalizing before editing any one module:

- **Everything flows through the admission pipeline in a fixed order**: ID assignment → redaction (content + index_line) → tool-output truncation (tool_output class only) → tool-output dedup (SHA-256 of final stored form) → token measurement → absolute admission check → apply policy-default priority → insert into WorkingSet. Every step logs against the assigned ID; rejected submissions still consume an ID and are explainable later, but store no content.
- **`balance()` is the only operation that evicts**; `render()` is pure and raises `UnbalancedError` if invariants don't hold rather than balancing implicitly. Eviction runs in strict passes: TTL (Pass A, includes restricted items) → per-class sub-budgets (Pass B, renderable only) → global (Pass C, renderable only), each with exact tie-break keys (ascending priority, descending token_size, ascending last_rendered_turn, ascending id). System-class and pinned items are never eviction candidates.
- **`mneme_meta` is opaque** — the kernel reads exactly one key, `render_restricted`. Restricted items stay working-set-visible but are excluded from all render budgets, sub-budgets, the contested pool, and render output; their size is reported separately via `explain.budget().restricted_nonrendered_tokens`.
- **All state is session-scoped**: `end_session()` exports working set + page store + log as plain data, then irreversibly clears everything. No module-level mutable state may leak between kernel instances.
- **The MutationLog is the single source of truth for `explain.*`** — explain answers are derived only from the log plus current state; if something can't be derived that way, it's out of scope (add no auxiliary state to make it derivable).
- **There is no delete or content-edit operation anywhere in the public API.** Content is immutable after admission; the only ways an item's visibility changes are evict (working → paged), recall (paged → working), and session export/clear.
