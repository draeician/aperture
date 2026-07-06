# Aperture Phase 0 Implementation Order

Execute strictly in sequence. Each step names the files to create and the tests to write. All tests written so far must pass before advancing. No code beyond the named files per step. PHASE0_BRIEF.md is authoritative for all behavior.

## Step 1 — Foundations: errors, items, policy

Create: aperture/errors.py, aperture/items.py, aperture/policy.py, aperture/__init__.py (empty exports for now), pyproject.toml, tests/conftest.py (policy and submission factory fixtures only).

- errors.py: ApertureError base and all seven subclasses per the Error Types section, with the payload fields named there (PinOverflowError carries pinned total and ceiling; ExpiredRecallError carries item id and expiry turn).
- items.py: SourceClass, ItemState, Structure, TruncationMode, EventKind enums; Provenance, Submission (frozen); ContextItem (mutable, kernel-owned) with all fields from Data Types.
- policy.py: Policy frozen dataclass, RedactionRule, construction-time validation (budget_total > 0, subbudget fractions in (0,1], system absent from class_eviction_order, system first in render_order, page_index present in render_order, regex compilation, tokenizer_id format).

Write tests: policy validation cases inside a new tests/test_lifecycle.py section limited to construction (invalid policies raise PolicyError; valid defaults construct). Full lifecycle tests come later; keep this file additive.

## Step 2 — Tokenizer

Create: aperture/tokenizer.py.

- Tokenizer protocol: count(text) -> int and a documented split capability sufficient for truncation math.
- Whitespace tokenizer (stdlib, deterministic, default).
- tiktoken adapter behind a guarded import keyed by "tiktoken:<encoding>"; ImportError at policy use time raises PolicyError naming the optional extra.

Write tests: token counting determinism and adapter fallback behavior appended to tests/test_lifecycle.py construction section (guarded skip if tiktoken absent must NOT exist for core paths — only the adapter test may skip).

## Step 3 — Mutation log

Create: aperture/log.py.

- MutationEvent frozen dataclass; append-only MutationLog with monotonic seq; filtered reads by kind and item_id; export to plain data.
- No mutation or deletion APIs.

Write tests: tests/test_explain.py started with log-only cases: append ordering, filtering, immutability of returned data.

## Step 4 — Containers

Create: aperture/working_set.py, aperture/page_store.py.

- WorkingSet: insert, membership, iteration in ascending id, per-class renderable/unpinned token totals, restricted-token total (items whose mneme_meta has render_restricted True), invariant assertions (no duplicate ids, states consistent).
- PageStore: insert on eviction, remove on recall, byte-identity guarantee (store the ContextItem, never copies of content), iteration in eviction order.

Write tests: container-level cases in a new tests/test_page_index.py section (store ordering) and tests/test_budget.py section (token totals split renderable vs restricted). Keep sections additive.

## Step 5 — Governance pipeline

Create: aperture/governance.py.

Implement in isolation, in pipeline order per the Admission Pipeline section:
- Redaction pass (content + index_line), logging redacted per rule with match counts only.
- Tool-output truncation: head, tail, head_tail; lines and json structural handling with fallback; accurate elision counts under the session tokenizer; truncated event payload.
- Tool-output dedup: SHA-256 of final stored form; stub content format; deduped event with both ids; hash registry spanning the session.

Write tests: tests/test_redaction.py (rule ordering, marker format, log payload contains no matched text) and tests/test_tool_output.py (all truncation modes, structure handling, dedup of identical oversized outputs, stub byte-consistency).

## Step 6 — Budget governor

Create: aperture/budget.py.

- effective_ceiling, reserved, contested_pool math per the Eviction Algorithm section, with restricted items excluded from all render-budget figures.
- Pin-overflow detection (raise before any eviction).
- Expiry flagging; Pass A (TTL, includes restricted items), Pass B (sub-budgets, renderable only), Pass C (global, renderable only); exact tie-break keys; page_index_cost recomputation after every eviction; BalanceReport contents (evicted ids in order, triggers, displaced_by).
- balance() idempotence: no-op second call logs nothing.

Write tests: tests/test_eviction.py (pass ordering, tie-break crafting, system immunity, restricted skip in B/C but eligible in A, termination under index growth) and tests/test_pins.py (overflow raises without eviction, eviction immunity) and remaining tests/test_budget.py cases (ceiling, sub-budgets, margin, idempotence).

## Step 7 — Page index

Create: aperture/page_index.py.

- Entry format, mechanical line generation (60-char hard cut, newline collapse), restricted-entry suppression format, header/footer, collapse rule with index_collapsed logging, zero-cost-when-empty, cost accounting with the session tokenizer.

Write tests: remaining tests/test_page_index.py cases (formats, collapse, cap, recallability of collapsed ids, zero-paged zero-cost).

## Step 8 — Renderer

Create: aperture/renderer.py.

- Balanced-state precondition (UnbalancedError), fixed class ordering per policy.render_order with page_index slot, ascending id within class, role mapping, [{source_class} #{id}] label prefixes, restricted-item exclusion from output and totals, RenderResult (message array, ordered manifest, total rendered tokens), byte-identical consecutive renders.

Write tests: tests/test_render.py in full.

## Step 9 — Facade

Create: aperture/kernel.py; finalize aperture/__init__.py exports.

Wire exactly the ten facade operations from Core Operations:
- submit: full Admission Pipeline order (ID first; rejected consumes ID; rejection metadata logged; content returned not stored).
- balance, render (purity effects only), recall (atomic with internal balance; ExpiredRecallError precheck; working-id silent no-op), pin, extend_ttl (positive turns; ttl_extended; expired-flag clearing rule), next_turn, recall_request_schema / handle_recall_request, end_session (SessionExport then irreversible clear; SessionClosedError thereafter).
- Atomicity everywhere: validate first, mutate after; failed operations mutate nothing and log nothing.

Write tests: tests/test_recall.py in full, tests/test_ttl.py in full, remaining tests/test_lifecycle.py (state-machine walk, ID gaps from rejections, end_session completeness, post-close raises, instance isolation), tests/test_mneme_opacity.py in full.

## Step 10 — Explain API

Create: aperture/explain.py.

- item, absence (four outcomes including rejected-ID metadata), render (manifest, per-class totals, diff), budget (with restricted_nonrendered_tokens as a separate field), log passthrough, starved (renderable items only).
- Derive everything from MutationLog plus current state; add no auxiliary state.

Write tests: remaining tests/test_explain.py (ground-truth comparison harness recording events independently during driven sessions; absence coverage; rejected-ID answers; UnknownItemError; starved excludes restricted).

## Step 11 — Determinism and fuzz

Create: fuzz session generator in tests/conftest.py (seeded, driving all facade operations including rejections, pins, TTL, restricted items, recalls, collapses).

Write tests: tests/test_determinism.py (50 sessions x 3 replays: identical logs, eviction sequences, render bytes), plus the no-sockets/no-state-files assertion in tests/test_lifecycle.py, plus fuzz-driven surface grep in tests/test_redaction.py.

Treat any flake as an implementation bug. Lock all seeds.

## Step 12 — Acceptance sweep

No new files. Run the full suite; verify every Acceptance Criteria bullet in PHASE0_BRIEF.md by test or inspection; confirm stdlib-only import; confirm tiktoken absence changes only token counts; confirm end_session leaves no trace observable by a fresh kernel instance.
