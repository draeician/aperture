"""Enums and data types: SourceClass, ItemState, Structure, TruncationMode,
EventKind, Provenance, Submission, ContextItem."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class SourceClass(StrEnum):
    system = "system"
    user_fact = "user_fact"
    conversation = "conversation"
    tool_output = "tool_output"
    scratch = "scratch"
    mneme_import = "mneme_import"
    page_index = "page_index"


class ItemState(StrEnum):
    working = "working"
    paged = "paged"
    rejected = "rejected"


class Structure(StrEnum):
    plain = "plain"
    lines = "lines"
    json = "json"


class TruncationMode(StrEnum):
    head = "head"
    tail = "tail"
    head_tail = "head_tail"


class EventKind(StrEnum):
    admitted = "admitted"
    rejected = "rejected"
    truncated = "truncated"
    deduped = "deduped"
    redacted = "redacted"
    evicted = "evicted"
    recalled = "recalled"
    expired_flagged = "expired_flagged"
    ttl_extended = "ttl_extended"
    rendered = "rendered"
    session_export = "session_export"
    index_collapsed = "index_collapsed"


@dataclass(frozen=True)
class Provenance:
    submitted_by: str
    origin: str | None = None
    mneme_record_id: str | None = None


@dataclass(frozen=True)
class Submission:
    """Host-facing submission handed to Aperture.submit()."""

    source_class: SourceClass
    content: str
    provenance: Provenance
    priority: int | None = None
    pinned: bool = False
    ttl_turns: int | None = None
    index_line: str | None = None
    mneme_meta: Mapping[str, object] | None = None
    structure: Structure = Structure.plain


@dataclass
class ContextItem:
    """Kernel-owned, mutable record for an admitted item."""

    id: int
    source_class: SourceClass
    content: str
    token_size: int
    priority: int
    pinned: bool
    ttl_turns: int | None
    provenance: Provenance
    index_line: str
    state: ItemState
    admitted_turn: int
    last_rendered_turn: int | None
    mneme_meta: Mapping[str, object] | None
