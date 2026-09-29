"""Enums and public item/submission data types."""

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
    memory = "memory"
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
class SourceRef:
    """Opaque application-owned reference to an external source record."""

    source_system: str
    source_id: str


@dataclass(frozen=True)
class Provenance:
    submitted_by: str
    origin: str | None = None
    mneme_record_id: str | None = None


@dataclass(frozen=True)
class Submission:
    """Host-facing submission handed to Aperture.submit().

    Generic integration fields are appended after the Phase 0 fields so
    existing positional construction remains compatible. mneme_meta is
    retained only as a legacy compatibility seam.
    """

    source_class: SourceClass
    content: str
    provenance: Provenance
    priority: int | None = None
    pinned: bool = False
    ttl_turns: int | None = None
    index_line: str | None = None
    mneme_meta: Mapping[str, object] | None = None
    structure: Structure = Structure.plain
    source_ref: SourceRef | None = None
    source_metadata: Mapping[str, object] | None = None
    render_restricted: bool = False
    application_key: str | None = None
    role: str | None = None


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
    mneme_meta: Mapping[str, object] | None = None
    source_ref: SourceRef | None = None
    source_metadata: Mapping[str, object] | None = None
    render_restricted: bool = False
    application_key: str | None = None
    role: str | None = None


def is_legacy_mneme_restricted(item: ContextItem) -> bool:
    """Return the historical mneme_meta restriction signal, if present."""

    return bool(item.mneme_meta is not None and item.mneme_meta.get("render_restricted") is True)


def is_render_restricted(item: ContextItem) -> bool:
    """Return the effective render restriction.

    New integrations use the first-class item flag. The legacy metadata
    signal remains supported so existing Phase 0 callers keep identical
    behavior.
    """

    return bool(item.render_restricted or is_legacy_mneme_restricted(item))
