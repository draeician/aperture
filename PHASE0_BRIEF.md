# Aperture Phase 0 Implementation Brief

This is the authoritative specification. Tests define correctness.

## Goal

Implement Aperture as a pure Python 3.11+ library: a deterministic, in-process context governor with admission, budget enforcement, non-destructive eviction, page index, recall, TTL extension, tool-output governance, pattern redaction, pure rendering, and a queryable explain API backed by an append-only session mutation log. No model calls, no network, no persistence beyond the process.

## Non-Goals

- No inference of any kind (summarization, scoring, embedding, semantic dedup).
- No retrieval or search.
- No durable storage: nothing survives end_session(); the page store is in-memory scratch.
- No MNEME client; mneme_meta is carried opaquely; only the render_restricted key is read.
- No framework adapters, no tool registration, no provider SDK dependencies.
- No replay: the mutation log justifies decisions; it cannot reconstruct state and no API implies it can.
- No async, no threads, no multi-agent, no multi-session.
- No policy DSL: policy is a plain frozen dataclass loaded from a dict.

## Repository Layout

    aperture/
        __init__.py          # public API surface only
        items.py             # ContextItem, enums, states
        policy.py            # Policy and sub-configs, validation
        working_set.py       # WorkingSet
        page_store.py        # PageStore
        page_index.py        # PageIndex
        budget.py            # BudgetGovernor (balance/eviction)
        governance.py        # redaction, tool-output truncation, dedup (admission pipeline)
        renderer.py          # Renderer
        log.py               # MutationLog, event types
        explain.py           # Explain/Why API (reads MutationLog + current state)
        tokenizer.py         # tokenizer protocol + whitespace default + optional tiktoken adapter
        errors.py            # error types
        kernel.py            # Aperture facade wiring the above
    tests/
        test_determinism.py
        test_budget.py
        test_pins.py
        test_eviction.py
        test_page_index.py
        test_recall.py
        test_ttl.py
        test_tool_output.py
        test_redaction.py
        test_render.py
        test_explain.py
        test_lifecycle.py
        test_mneme_opacity.py
        conftest.py          # fixtures: policies, item factories, seeded fuzz session generator
    pyproject.toml           # stdlib-only core; tiktoken as optional extra [tokens]

## Data Types

All frozen dataclasses unless mutation is required; all enums are StrEnum.

SourceClass (enum): system, user_fact, conversation, tool_output, scratch, mneme_import, page_index. The page_index class is kernel-generated only; host submission with this class raises InvalidItemError.

ItemState (enum): working, paged, rejected.

ContextItem (mutable dataclass, kernel-owned):
- id: int — monotonically increasing per session, assigned at submission before governance, never reused. Rejected submissions consume IDs; gaps in the working set are expected.
- source_class: SourceClass
- content: str — post-redaction, post-truncation form; the only stored form; immutable after admission.
- token_size: int — measured after governance.
- priority: int — host-declared; else policy default per source class. Never computed from content.
- pinned: bool
- ttl_turns: int | None — turns until eviction-eligible regardless of priority.
- provenance: Provenance
- index_line: str — host-supplied, else mechanically generated (see Page Index Rules).
- state: ItemState
- admitted_turn: int
- last_rendered_turn: int | None
- mneme_meta: Mapping[str, object] | None — opaque; the kernel reads exactly one key: render_restricted (bool).

Provenance (frozen): submitted_by: str (host label), origin: str | None, mneme_record_id: str | None.

Submission (frozen, host-facing): source_class, content, optional priority, pinned, ttl_turns, index_line, provenance, mneme_meta, and optional structure: Structure where Structure (enum) is plain, lines, json.

Turn counter: owned by the facade; incremented only by next_turn(). All TTL and recency logic reads this counter.

MutationEvent (frozen): seq: int, turn: int, kind: EventKind, item_id: int | None, payload: Mapping[str, object].

EventKind (enum): admitted, rejected, truncated, deduped, redacted, evicted, recalled, expired_flagged, ttl_extended, rendered, pin_overflow, session_export, index_collapsed.

The rejected event payload must include: source_class, token_size (post-governance), rejection reason, turn — sufficient for explain.absence(item_id) to answer for rejected IDs. Rejected content is not stored anywhere; it is returned to the host in the AdmissionResult only.

## Policy Model

Policy (frozen dataclass), validated at construction, immutable for the session. Invalid policy raises PolicyError before any session state exists.

- budget_total: int — hard token ceiling per render; > 0.
- reply_headroom: int — reserved, subtracted first.
- page_index_budget: int — cap for the rendered index block.
- class_defaults: Mapping[SourceClass, int] — default priorities.
- class_subbudgets: Mapping[SourceClass, float] — optional per class; each in (0, 1]; caps, not allocations; no sum constraint.
- class_eviction_order: tuple[SourceClass, ...] — default (scratch, tool_output, conversation, mneme_import, user_fact). system must not appear; validation error if it does.
- tool_output_max_tokens: int
- tool_truncation: TruncationMode — enum: head, tail, head_tail (default head_tail, 50/50 split with elision marker between).
- redaction_rules: tuple[RedactionRule, ...] — each: rule_id: str, pattern: str (compiled with re.compile at policy load), label: str (used in the [REDACTED:{label}] marker).
- render_order: tuple[SourceClass, ...] — fixed class ordering for render; system first mandatory; page_index position explicit.
- tokenizer_id: str — "whitespace" (default, stdlib) or "tiktoken:<encoding>" (optional extra). Recorded in the log at session start.
- token_safety_margin: float — default 0.0; effective ceiling = floor(budget_total * (1 - margin)).

## Core Operations

Facade Aperture(policy) exposes exactly:

1. submit(submission) -> AdmissionResult — runs the Admission Pipeline (below). AdmissionResult carries the item id and, on rejection, the reason plus the governed content returned to the host (not stored).
2. balance() -> BalanceReport — runs the Eviction Algorithm until all budget invariants hold. The only operation that evicts. Idempotent: a second consecutive call with no intervening mutation is a no-op and logs nothing.
3. render() -> RenderResult — pure; raises UnbalancedError if invariants do not hold (render never balances implicitly). Returns the message array, the ordered item-id manifest, and total rendered tokens. Sole permitted state effects: last_rendered_turn updates and one rendered log event. Two consecutive renders are byte-identical.
4. recall(item_id) -> BalanceReport — page-in per Recall Rules; runs balance() internally as one atomic operation.
5. pin(item_id, pinned: bool) — toggles; a resulting pin-overflow condition is detected at the next balance().
6. extend_ttl(item_id, turns) -> None — valid for working or paged items; turns must be positive, else InvalidItemError; logs ttl_extended with {old_ttl, new_ttl}; clears expired status if the new TTL horizon is in the future relative to the current turn.
7. next_turn() -> int
8. explain.* — see Explain Contract.
9. recall_request_schema() / handle_recall_request(payload) — see Recall Rules.
10. end_session() -> SessionExport — returns working set, page store contents, and full log as plain data structures; then irreversibly clears all state. Any call after end_session raises SessionClosedError.

There is no delete operation. There is no content-edit operation. Failed operations mutate nothing and log nothing.

## Admission Pipeline

Exact order, no variation:

1. Assign ID (monotonic; rejected submissions consume IDs; IDs never reused).
2. Redaction — applied to content and to host-supplied index_line, all source classes.
3. Tool-output truncation — tool_output class only, per Tool Output Governance. Applies regardless of pin flag.
4. Tool-output dedup — SHA-256 of the final stored form (post-redaction, post-truncation).
5. Token measurement — with the session tokenizer.
6. Absolute admission check — reject if post-governance token_size exceeds the contested pool, pinned or not. Log rejected with required metadata; return content to host; store nothing.
7. Apply policy defaults (priority per source class if undeclared).
8. Insert into WorkingSet; log admitted.

Every step logs against the assigned ID.

## Eviction Algorithm

Runs inside balance(). Definitions:
- effective_ceiling = floor(budget_total * (1 - token_safety_margin))
- reserved = pinned_renderable_total + page_index_cost + reply_headroom
- contested_pool = effective_ceiling - reserved

Render-budget accounting excludes restricted items entirely (see Render Contract): items with mneme_meta.render_restricted == True do not count toward any render budget, sub-budget, reserved total, or the contested pool, and are never eviction candidates on budget grounds. They remain working-set-visible and are reported separately by explain.budget() as restricted_nonrendered_tokens. TTL still applies to restricted items (Pass A can evict them if expired).

1. If pinned_renderable_total + reply_headroom > effective_ceiling: log pin_overflow, raise PinOverflowError. No eviction occurs.
2. Flag expired items: any working item with TTL where current_turn - admitted_turn >= ttl_turns is flagged (log expired_flagged once per item).
3. Pass A — TTL: while any invariant is violated and expired unpinned items exist, evict expired items oldest-admitted first, ties by ascending id.
4. Pass B — sub-budgets: for each class with a sub-budget, in class_eviction_order, while that class's renderable unpinned working tokens exceed floor(fraction * contested_pool): evict within the class by (ascending priority, then descending token_size, then ascending last_rendered_turn with None as -1, then ascending id).
5. Pass C — global: while renderable unpinned working tokens exceed contested_pool: take classes in class_eviction_order; within the current class evict by the Pass B key; advance to the next class when the current class has no renderable unpinned working items.
6. Recompute page_index_cost after every single eviction and re-check invariants. Termination is guaranteed: each eviction strictly reduces renderable working tokens and index cost is capped.
7. Eviction = state paged, move to PageStore, add index entry, log evicted with payload {trigger: "ttl" | "subbudget" | "global" | "recall", displaced_by: item_id | None}.

Pinned and system-class items are never candidates in any pass. Determinism: identical state and policy produce the identical eviction id sequence.

## Page Index Rules

- One entry per paged item: #{id} [{source_class}] {index_line} ({token_size} tok).
- Mechanical index line when host omits it: first 60 characters of content with newlines collapsed to spaces, hard cut, no other processing. Never generated from meaning.
- The index renders as one fixed block: header line "--- PAGED CONTEXT (recall by id) ---", entries in eviction order (oldest first), collapse footer if applicable.
- Collapse: if index token cost exceeds page_index_budget, collapse oldest entries into the single footer "+ {n} older paged items (ids {min}..{max}; use explain)" until under cap; log index_collapsed with the collapsed ids. Collapsed items remain fully recallable by id.
- Zero paged items: the block is omitted entirely and costs zero tokens.
- Paged items with render_restricted == True appear as #{id} [mneme_import] (restricted) ({size} tok) — index line suppressed, entry present.

## Recall Rules

- recall(item_id): item must exist in PageStore, else UnknownItemError. If the id refers to a working item, silent no-op returning a trivial report, logging nothing.
- Recall of an item currently flagged expired raises ExpiredRecallError carrying the item id and expiry turn; nothing mutates, nothing logs. Recovery path: extend_ttl(item_id, turns) — valid on paged items, clears the expired flag if the new horizon is in the future — then recall(item_id).
- On successful recall: state working, removed from PageStore and index, content byte-identical to admitted form, then balance() runs atomically. Evictions it triggers log with trigger "recall" and displaced_by set to the recalled id.
- Agent-driven recall: recall_request_schema() returns a static dict describing the tool-call shape {"name": "aperture_recall", "input": {"item_id": int}}; handle_recall_request(payload) validates, calls recall, and returns a plain-text confirmation or error string. The kernel registers nothing anywhere; the host supplies all plumbing.
- No automatic or predictive page-in exists.

## Tool Output Governance

Admission-time, tool_output class only, applied in pipeline order (after redaction):

1. Size cap: if token count > tool_output_max_tokens, truncate per tool_truncation mode. Elision marker: "[... {n} tokens elided ...]" with n accurate under the session tokenizer. structure == lines: cut only at newline boundaries nearest the budget split. structure == json: attempt json.loads; if it parses to a list, drop middle elements at element boundaries and insert the marker as a string element; otherwise fall back to lines behavior. No other structural intelligence. Log truncated with {original_tokens, kept_tokens, mode, structure}.
2. Hash dedup: SHA-256 of the final stored form (post-redaction, post-truncation). On match with any prior admitted tool_output hash this session (working or paged), admit a stub instead: content "[duplicate of item #{id}]", log deduped with both ids. The stub guarantee: the referenced item's stored content is byte-identical to what this submission would have stored.

Truncation applies to pinned submissions identically; pin grants no admission-time exemption.

## Redaction Rules

- First governance step, all source classes, content and host index_line.
- Rules applied in policy order via re.sub, replacement [REDACTED:{label}].
- Log redacted per rule per item with {rule_id, match_count} only — never matched text, never a snippet.
- One-way: no surface (WorkingSet, PageStore, index, log payloads, render output, session export) may contain pre-redaction content. The host receives no pre-redaction echo.
- Pattern rules only. No inference-based detection.

## Render Contract

- Precondition: balanced state, else UnbalancedError.
- Output: ordered list of {"role": str, "content": str} dicts. Role mapping fixed: system -> "system"; all else -> "user", each item's content prefixed with the label line [{source_class} #{id}].
- Ordering: classes in policy.render_order; within a class, ascending admission id. The page index block occupies the page_index slot in render_order.
- Restricted items (mneme_meta.render_restricted == True) are excluded from render output and excluded from render token budgets (working-set-visible, render-budget-invisible). Rationale: their carrying cost is reported, not charged; explain.budget() surfaces restricted_nonrendered_tokens so the host sees exactly what it is holding without rendering.
- Purity: no state changes except last_rendered_turn updates and one rendered log event carrying the ordered manifest and total rendered tokens. Two consecutive renders with no intervening mutation are byte-identical.
- balance() is the only evicting operation; render() never balances.

## Explain Contract

explain namespace, all returning plain dicts/lists (data, not prose). All answers derive from the MutationLog plus current state; if an answer cannot be derived from those, it is out of scope — add no auxiliary state.

- explain.item(item_id) — full event history plus current state, priority, pin, TTL status, turns present, renders appeared in. Works for rejected IDs (metadata from the rejected event).
- explain.absence(item_id) — one of {"reason": "rejected", ...metadata}, {"reason": "paged", "trigger": ..., "displaced_by": ...}, {"reason": "render_restricted"}, or UnknownItemError if the id was never assigned.
- explain.render(seq | "last") — manifest, per-class rendered token totals, diff vs. previous render (added, removed, recalled id lists).
- explain.budget() — current effective ceiling, reserved breakdown (pinned / index / headroom), contested pool, per-class renderable working totals, and restricted_nonrendered_tokens as a separate field.
- explain.log(kind=None, item_id=None) — filtered event list.
- explain.starved() — ids of renderable working items never rendered with current_turn - admitted_turn >= 2.

## Error Types

All subclass ApertureError:
- PolicyError — invalid policy at construction.
- InvalidItemError — bad submission (reserved class, empty content, negative TTL) or non-positive extend_ttl turns.
- PinOverflowError — pinned renderable total + headroom exceed effective ceiling; carries both numbers.
- UnbalancedError — render called on unbalanced state.
- UnknownItemError — id never assigned, or not in the expected store for the operation.
- ExpiredRecallError — recall attempted on an expired item; carries item id and expiry turn.
- SessionClosedError — any call after end_session.

No error is swallowed. Operations are atomic: validate first, mutate after; failed operations mutate nothing and log nothing.

## Test Suite

pytest; stdlib random with fixed seeds for fuzzing.

- test_determinism.py — seeded fuzz generator produces 50 random sessions; each replayed 3 times must yield identical logs, eviction sequences, and render bytes.
- test_budget.py — randomized loads never render above effective ceiling minus headroom; sub-budget caps enforced; safety margin honored; balance() idempotence; restricted tokens never counted in any budget figure except restricted_nonrendered_tokens.
- test_pins.py — pinned items appear in every render (unless restricted); pin-overflow raises and evicts nothing; unpin then balance recovers; pinned never evicted; no post-admission content mutation exists for any item; pinned oversized tool_output is truncated at admission like any other.
- test_eviction.py — TTL pass precedes sub-budget precedes global; ordering keys verified with crafted ties (equal priority: larger first; equal size: older-rendered first; equal: lower id first); system never evicted; restricted items skipped in Passes B/C but evictable in Pass A when expired; termination under index-growth pressure.
- test_page_index.py — every paged item indexed or in the collapse footer; index cost <= cap; zero-paged means zero cost; mechanical line generation; restricted-entry suppression format; collapse preserves recallability.
- test_recall.py — byte-identical restoration; recall-triggered eviction logs the causal chain; recall of a working id is a silent no-op; ExpiredRecallError path mutates and logs nothing; extend-then-recall path succeeds; handle_recall_request validation; unknown id raises.
- test_ttl.py — expiry flagging at exact boundary turn; extend_ttl on working and paged items; positive-turns validation; expired flag cleared only when the new horizon is in the future; ttl_extended payload correctness.
- test_tool_output.py — head/tail/head_tail cuts with accurate elision counts; line-boundary cuts; JSON list element drops; JSON fallback to lines; dedup hashes the final stored form (two identical oversized outputs dedup; stub references byte-identical stored content); dedup spans working and paged.
- test_redaction.py — after fuzzed sessions, grep every surface (working content, page store, index lines, log payloads, render output, export) for active patterns: zero hits; per-rule log counts correct; host index lines redacted.
- test_render.py — purity (consecutive renders identical); UnbalancedError when dirty; fixed ordering; role mapping; label prefixes; restricted items absent from output and from rendered token totals while present in the working set.
- test_explain.py — independently recorded ground truth from fuzzed sessions matches explain output exactly; absence covers rejected / paged / render_restricted; explain.item works for rejected IDs; UnknownItemError for never-assigned ids; starved detection excludes restricted items.
- test_lifecycle.py — full state-machine walk per item; ID monotonicity with rejection gaps; end_session export completeness; post-close calls raise SessionClosedError; two kernel instances share no state; a fuzz session opens no sockets and writes no files (excluding pytest's own).
- test_mneme_opacity.py — arbitrary mneme_meta round-trips byte-identical through page/recall/export; behavior differs only on render_restricted.

## Implementation Order

Follow IMPLEMENTATION_ORDER.md exactly, one step at a time, tests green before advancing.

## Acceptance Criteria

Phase 0 is done when:
- All thirteen test files pass, including 50-session fuzz determinism at 3 replays each.
- The library imports with stdlib only; tiktoken's absence changes nothing but token counts.
- No public API can destroy content, persist state, open a network connection, or invoke a model — verified by inspection and by the lifecycle test asserting no sockets or state files during a fuzz session.
- explain.absence and explain.item answer correctly for every assigned id in every fuzzed session, including rejected ids.
- A reviewer can trace any token in any render output to an admitted event and a chain of governance events using only the exported log.
- end_session leaves nothing: a kernel constructed immediately after shows no trace of the first.
