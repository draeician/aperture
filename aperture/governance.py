"""Governance pipeline helpers: redaction, tool-output truncation, tool-output dedup.

These are isolated pipeline-step helpers, not the wired admission
pipeline. They do not assign ids and do not insert into WorkingSet;
the future kernel facade is responsible for id assignment, invoking
these in Admission Pipeline order, and everything else in that
section of PHASE0_BRIEF.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping, MutableMapping

from aperture.items import EventKind, SourceClass, Structure, TruncationMode
from aperture.log import MutationLog
from aperture.policy import RedactionRule
from aperture.tokenizer import Tokenizer


# --- Redaction --------------------------------------------------------


def redact(
    *,
    content: str,
    index_line: str | None,
    rules: tuple[RedactionRule, ...],
    log: MutationLog,
    item_id: int,
    turn: int,
) -> tuple[str, str | None]:
    """Apply redaction_rules, in policy order, to content and index_line.

    Each rule is applied via re.sub with replacement "[REDACTED:{label}]".
    A rule that matches at least once (counting content and index_line
    together) logs exactly one 'redacted' event for this item with
    {rule_id, match_count}; matched text is never logged.
    """
    for rule in rules:
        content, content_count = re.subn(
            rule.pattern, lambda m, label=rule.label: f"[REDACTED:{label}]", content
        )
        if index_line is not None:
            index_line, index_count = re.subn(
                rule.pattern, lambda m, label=rule.label: f"[REDACTED:{label}]", index_line
            )
        else:
            index_count = 0

        match_count = content_count + index_count
        if match_count > 0:
            log.append(
                turn=turn,
                kind=EventKind.redacted,
                item_id=item_id,
                payload={"rule_id": rule.rule_id, "match_count": match_count},
            )

    return content, index_line


# --- Tool-output truncation --------------------------------------------


def _take_head(unit_tokens: list[int], budget: int) -> int:
    """Count of leading units whose cumulative token size fits budget."""
    total = 0
    count = 0
    for size in unit_tokens:
        if total + size > budget:
            break
        total += size
        count += 1
    return count


def _take_tail(unit_tokens: list[int], budget: int) -> int:
    """Count of trailing units whose cumulative token size fits budget."""
    total = 0
    count = 0
    for size in reversed(unit_tokens):
        if total + size > budget:
            break
        total += size
        count += 1
    return count


def _select_head_tail_counts(
    unit_tokens: list[int], max_tokens: int, mode: TruncationMode
) -> tuple[int, int]:
    """Choose how many leading/trailing whole units to keep, without
    exceeding max_tokens (50/50 split for head_tail), never overlapping.
    """
    total_units = len(unit_tokens)

    if mode == TruncationMode.head:
        return _take_head(unit_tokens, max_tokens), 0

    if mode == TruncationMode.tail:
        return 0, _take_tail(unit_tokens, max_tokens)

    head_budget = max_tokens // 2
    tail_budget = max_tokens - head_budget
    head_count = _take_head(unit_tokens, head_budget)
    tail_count = _take_tail(unit_tokens[head_count:], tail_budget)
    if head_count + tail_count > total_units:
        tail_count = total_units - head_count
    return head_count, tail_count


def _truncate_plain(
    content: str, max_tokens: int, mode: TruncationMode, tokenizer: Tokenizer, original_tokens: int
) -> tuple[int, str]:
    tokens = tokenizer.split(content)
    unit_tokens = [1] * len(tokens)
    head_count, tail_count = _select_head_tail_counts(unit_tokens, max_tokens, mode)

    head_tokens = tokens[:head_count]
    tail_tokens = tokens[len(tokens) - tail_count :] if tail_count else []

    kept_no_marker = " ".join(head_tokens + tail_tokens)
    kept_tokens = tokenizer.count(kept_no_marker) if kept_no_marker else 0
    marker = f"[... {original_tokens - kept_tokens} tokens elided ...]"

    parts = [p for p in (" ".join(head_tokens), marker, " ".join(tail_tokens)) if p]
    return kept_tokens, " ".join(parts)


def _truncate_lines(
    content: str, max_tokens: int, mode: TruncationMode, tokenizer: Tokenizer, original_tokens: int
) -> tuple[int, str]:
    lines = content.split("\n")
    unit_tokens = [tokenizer.count(line) for line in lines]
    head_count, tail_count = _select_head_tail_counts(unit_tokens, max_tokens, mode)

    head_lines = lines[:head_count]
    tail_lines = lines[len(lines) - tail_count :] if tail_count else []

    kept_no_marker = "\n".join(head_lines + tail_lines)
    kept_tokens = tokenizer.count(kept_no_marker) if kept_no_marker else 0
    marker = f"[... {original_tokens - kept_tokens} tokens elided ...]"

    parts = [p for p in ("\n".join(head_lines), marker, "\n".join(tail_lines)) if p]
    return kept_tokens, "\n".join(parts)


def _truncate_json(
    content: str, max_tokens: int, mode: TruncationMode, tokenizer: Tokenizer, original_tokens: int
) -> tuple[int, str] | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None

    elements = parsed
    unit_tokens = [tokenizer.count(json.dumps(element)) for element in elements]
    head_count, tail_count = _select_head_tail_counts(unit_tokens, max_tokens, mode)

    head_elements = elements[:head_count]
    tail_elements = elements[len(elements) - tail_count :] if tail_count else []

    kept_no_marker = json.dumps(head_elements + tail_elements)
    kept_tokens = tokenizer.count(kept_no_marker)
    marker = f"[... {original_tokens - kept_tokens} tokens elided ...]"

    final_elements = head_elements + [marker] + tail_elements
    return kept_tokens, json.dumps(final_elements)


def truncate_tool_output(
    *,
    source_class: SourceClass,
    content: str,
    structure: Structure,
    max_tokens: int,
    mode: TruncationMode,
    tokenizer: Tokenizer,
    log: MutationLog,
    item_id: int,
    turn: int,
) -> str:
    """Truncate tool_output content that exceeds max_tokens.

    No-op (unchanged content, no log) for any source_class other than
    tool_output, and for content already within max_tokens. structure ==
    lines cuts only at newline boundaries; structure == json drops list
    elements at element boundaries, falling back to lines behavior if
    the content does not parse as a JSON list. No other structural
    intelligence is applied.
    """
    if source_class != SourceClass.tool_output:
        return content

    original_tokens = tokenizer.count(content)
    if original_tokens <= max_tokens:
        return content

    if structure == Structure.json:
        result = _truncate_json(content, max_tokens, mode, tokenizer, original_tokens)
        if result is None:
            result = _truncate_lines(content, max_tokens, mode, tokenizer, original_tokens)
    elif structure == Structure.lines:
        result = _truncate_lines(content, max_tokens, mode, tokenizer, original_tokens)
    else:
        result = _truncate_plain(content, max_tokens, mode, tokenizer, original_tokens)

    kept_tokens, final_content = result

    log.append(
        turn=turn,
        kind=EventKind.truncated,
        item_id=item_id,
        payload={
            "original_tokens": original_tokens,
            "kept_tokens": kept_tokens,
            "mode": mode,
            "structure": structure,
        },
    )
    return final_content


# --- Tool-output dedup ---------------------------------------------------


def dedup_tool_output(
    *,
    source_class: SourceClass,
    content: str,
    hash_registry: Mapping[str, int],
    item_id: int,
    log: MutationLog,
    turn: int,
) -> str:
    """Dedup tool_output content by SHA-256 of its final stored form.

    No-op for any source_class other than tool_output. hash_registry is
    read-only here: this is a lookup against hashes the caller has
    already registered (via register_tool_output_hash) for prior
    *admitted* tool_output content (working and paged alike). This
    helper never registers a hash itself, so a governed-but-later-
    rejected submission can never become a dedup target. On a match,
    logs 'deduped' and returns the stub "[duplicate of item #{id}]"
    referencing the original item; otherwise returns content unchanged.
    """
    if source_class != SourceClass.tool_output:
        return content

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    original_item_id = hash_registry.get(digest)
    if original_item_id is None:
        return content

    log.append(
        turn=turn,
        kind=EventKind.deduped,
        item_id=item_id,
        payload={"duplicate_item_id": item_id, "original_item_id": original_item_id},
    )
    return f"[duplicate of item #{original_item_id}]"


def register_tool_output_hash(
    hash_registry: MutableMapping[str, int], content: str, item_id: int
) -> str:
    """Register content's SHA-256 as a future dedup target for item_id.

    Callers (the admission pipeline) must call this only after content
    is successfully admitted, using the exact final stored form, so
    that a rejected submission never becomes a dedup target. Returns
    the computed hex digest.
    """
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    hash_registry[digest] = item_id
    return digest
