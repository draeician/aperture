# Aperture

**Every token in the prompt, on purpose.**

Aperture is a deterministic, in-process Python 3.11+ context governor for LLM applications. It controls exactly which structured context items enter the next model call, under explicit token budget and policy constraints, and it can explain every inclusion and exclusion.

## What Aperture Is

- A runtime prompt assembly governor: typed context items in, budgeted message array out.
- A budget enforcer: hard token ceilings, per-source-class sub-budgets, reply headroom.
- A non-destructive evictor: items leave the working set into a session-scoped page store; nothing is ever deleted.
- A page index: the model always sees a token-cheap list of what has been paged out and can request it back.
- A recall mechanism: paged items return byte-identical, with the causal chain logged.
- A tool-output pressure valve: size caps, structural truncation, hash dedup at admission.
- A deterministic redactor: pattern-based scrubbing at admission, one-way, logged.
- An answer to "why was this token in my prompt?": every admission, truncation, redaction, eviction, recall, and render is recorded in a session mutation log queryable through the explain API.

All behavior is deterministic. Same items, same policy, same output — byte for byte. Zero model calls, zero network, stdlib-only core (tiktoken is an optional extra for token counting).

## What Aperture Is Not

- **Not durable memory.** All state is session-scoped scratch, discarded at session end.
- **Not RAG.** Aperture does not search, rank, embed, or fetch. It governs what the host hands it.
- **Not an agent framework.** No tool registration, no loops, no provider SDKs, no adapters.
- **Not intelligent.** No inference anywhere. Priority is declared, never computed from content.

## The MNEME Boundary

MNEME is the durable governed memory substrate. It owns the append-only journal, replayable derived state, ACL/policy metadata, lifecycle/archive, conflicts/links, and durable memory provenance.

Aperture owns runtime prompt assembly, render budget governance, in-session page index and recall, tool-output pressure control, render manifests, and render provenance ("why was this token in the prompt?").

In one sentence: **MNEME remembers; Aperture budgets.**

Flow direction is MNEME → host → Aperture. The host queries MNEME and submits selected records as items; Aperture carries MNEME metadata opaquely, honors exactly one signal (render restriction), and never reads from or writes to MNEME. At session end Aperture exports its state to the host; whether anything is journaled into MNEME is the host's decision.

## Phase 0 Scope

Admission pipeline (ID → redaction → tool-output governance → measurement → admission check), pinning, deterministic eviction, page index with collapse rules, host- and agent-driven recall, TTL with explicit extension, pure rendering to a provider-neutral message array, and the explain/why API backed by an append-only session mutation log. Single agent, single session, synchronous, local-only.

## Conceptual Example

A host submits a system prompt (pinned), user facts, conversation turns, and a 40,000-token tool output. Aperture redacts credentials by pattern, truncates the tool output at line boundaries to the policy cap, measures tokens, and admits everything. Before the model call, the host balances: two old scratch items and the eldest conversation turns are paged out, each leaving a one-line entry in the page index. Render produces the message array — system prompt, page index block, then remaining items in fixed order — guaranteed under budget. The model notices item #14 in the page index and issues a recall request; the host wires it through, #14 returns byte-identical, and the item it displaced is logged with the causal chain. Later, the host asks `explain.absence(9)` and gets: paged at turn 6, global pass, displaced by the recall of #14. No mystery, no magic.

## Generic Integration API

Aperture 0.2 adds an additive, product-neutral integration surface without changing the accepted Phase 0 defaults:

- `SourceClass.memory` is the generic durable/retrieved-context class; `mneme_import` remains for compatibility.
- `SourceRef(source_system, source_id)` carries opaque external references through governance into the structured render manifest.
- `Submission.render_restricted` is first-class. Legacy `mneme_meta.render_restricted` remains honored, but new integrations do not need MNEME-named fields.
- `source_metadata`, `application_key`, and `role` are generic application seams.
- `RenderResult.manifest` remains the legacy ordered item-id list. `RenderResult.manifest_entries` is the structured message-order manifest and includes generated page-index provenance so its token counts reconcile with `total_tokens`.
- `Policy(render_item_labels=False)` preserves submitted message content byte-for-byte in rendered messages; the Phase 0 label-prefixed format remains the default.
- `describe_component()`, `Aperture.describe()`, and `Aperture.session_descriptor()` expose implementation, protocol, tokenizer, capability, and policy identity without internal-module inspection.

The generic path has no Host or MNEME dependency. Artificial Person Host remains responsible for its adapter and for reporting actual rendered memory source references back to its memory component.

## Status

Phase 0 behavior is preserved. The generic integration API is version 1.0 in Aperture 0.2.0. PHASE0_BRIEF.md remains authoritative for Phase 0 invariants; tests define correctness.
