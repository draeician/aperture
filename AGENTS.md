# AGENTS.md — Aperture Repository Rules

Read this file completely before writing or editing code.

## Project Purpose

Aperture is a deterministic, in-process Python 3.11+ context governor for LLM applications.

It controls which structured context items enter the next model call under explicit token budget and policy constraints. It supports admission, pinning, non-destructive eviction, page index, recall, tool-output governance, deterministic redaction, pure rendering, and explain/why APIs.

Aperture is not memory, not RAG, not an agent framework, and not an inference system.

## Authoritative Spec

`PHASE0_BRIEF.md` is authoritative for Phase 0.

Do not implement behavior that is not specified there.

If this file and `PHASE0_BRIEF.md` conflict, stop and ask for clarification. Do not invent architecture.

## Hard Prohibitions

Do not add:

- inference
- summarization
- relevance scoring
- embeddings
- retrieval
- search
- durable memory
- database persistence
- file-backed state
- networking
- model calls
- async
- threads
- framework adapters
- provider SDK integrations
- MNEME clients
- replay/WAL semantics
- policy DSLs

The stdlib-only core requirement is mandatory. `tiktoken` may only be optional.

## MNEME Boundary

MNEME remembers. Aperture budgets.

MNEME owns durable governed memory, journal/replay, ACL/policy metadata, lifecycle/archive, conflicts/links, and memory provenance.

Aperture owns runtime prompt assembly, render budget governance, session-local page index/recall, tool-output pressure control, render manifests, and render provenance.

Aperture must never read from or write to MNEME in Phase 0.

`mneme_meta` is opaque. Aperture may read only `render_restricted`.

## Determinism Requirement

Same inputs, same policy, same operation order must produce identical:

- logs
- eviction sequences
- page index output
- render output
- explain output

Any nondeterminism is a correctness bug.

## Implementation Discipline

Follow `IMPLEMENTATION_ORDER.md` exactly.

Complete one step at a time.

For each step:

1. Create only the files named for that step.
2. Write only the tests named for that step.
3. Run the tests.
4. Do not advance until all tests written so far pass.

Do not skip ahead.

## Testing Is The Contract

Tests define correctness.

The Phase 0 implementation is complete only when all tests listed in `PHASE0_BRIEF.md` pass.

Do not weaken tests to make implementation pass.

Do not delete invariants.

Do not replace deterministic behavior with heuristic behavior.

## Logging Rules

The MutationLog explains successful session state changes and material governance decisions.

Failed operations mutate nothing and log nothing.

Invalid submissions fail before ID assignment.

Governable submissions receive an ID. If later rejected by admission policy, the rejected ID is logged and explainable.

## State Rules

All state is session-scoped.

`end_session()` exports state and then clears it irreversibly.

No Aperture-managed state may survive session end.

No module-level mutable state may leak between kernel instances.

## Public API Rules

The public API is the facade defined in `PHASE0_BRIEF.md`.

Do not expose internal containers as mutation surfaces.

Do not add delete or content-edit operations.

Do not add convenience APIs that bypass admission, balance, render, recall, or explain rules.

## Code Quality

Prefer boring, explicit code.

Use dataclasses and enums as specified.

Keep policy immutable for the session.

Keep content immutable after admission.

Use clear error types from `errors.py`.

No hidden side effects.

No magic.
