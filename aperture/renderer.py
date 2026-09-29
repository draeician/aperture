"""Renderer: pure rendering of the current WorkingSet into a message array.

Requires an externally-supplied balanced-state signal (is_balanced) so
this module never depends on BudgetGovernor directly and never calls
balance() or evicts. The only permitted state effects are
last_rendered_turn updates (for items actually rendered) and exactly
one rendered MutationLog event per call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from aperture.errors import UnbalancedError
from aperture.items import ContextItem, EventKind, SourceClass, SourceRef, is_render_restricted
from aperture.log import MutationLog
from aperture.page_index import PageIndex
from aperture.policy import Policy
from aperture.working_set import WorkingSet


@dataclass(frozen=True)
class RenderManifestEntry:
    """Structured provenance for one message actually dispatched to inference."""

    item_id: int | None
    source_ref: SourceRef | None
    source_class: SourceClass
    role: str
    token_count: int
    render_restricted: bool = False
    application_key: str | None = None
    generated: bool = False


@dataclass(frozen=True)
class RenderResult:
    messages: list[dict[str, str]]
    manifest: list[int]
    total_tokens: int
    manifest_entries: list[RenderManifestEntry] = field(default_factory=list)


class Renderer:
    """Renders WorkingSet + PageIndex into a provider-neutral message array."""

    def __init__(
        self,
        *,
        working_set: WorkingSet,
        policy: Policy,
        page_index: PageIndex,
        log: MutationLog,
        is_balanced: Callable[[], bool],
    ) -> None:
        self.working_set = working_set
        self.policy = policy
        self.page_index = page_index
        self.log = log
        self._is_balanced = is_balanced

    def render(self, current_turn: int) -> RenderResult:
        if not self._is_balanced():
            raise UnbalancedError("cannot render: budget invariants do not hold")

        items_by_class: dict[SourceClass, list[ContextItem]] = {}
        for item in self.working_set:
            if is_render_restricted(item):
                continue
            items_by_class.setdefault(item.source_class, []).append(item)

        messages: list[dict[str, str]] = []
        manifest: list[int] = []
        manifest_entries: list[RenderManifestEntry] = []
        rendered_items: list[ContextItem] = []
        total_tokens = 0
        class_totals: dict[SourceClass, int] = {}

        render_order = list(self.policy.render_order)
        if SourceClass.memory not in render_order:
            if SourceClass.mneme_import in render_order:
                insert_at = render_order.index(SourceClass.mneme_import)
            else:
                insert_at = render_order.index(SourceClass.page_index)
            render_order.insert(insert_at, SourceClass.memory)

        for source_class in render_order:
            if source_class == SourceClass.page_index:
                index_text = self.page_index.render()
                if index_text:
                    index_cost = self.page_index.cost()
                    messages.append({"role": "user", "content": index_text})
                    total_tokens += index_cost
                    manifest_entries.append(
                        RenderManifestEntry(
                            item_id=None,
                            source_ref=SourceRef(source_system="aperture", source_id="page_index"),
                            source_class=SourceClass.page_index,
                            role="user",
                            token_count=index_cost,
                            generated=True,
                        )
                    )
                continue

            for item in items_by_class.get(source_class, []):
                role = item.role or ("system" if source_class == SourceClass.system else "user")
                content = (
                    f"[{item.source_class} #{item.id}]\n{item.content}"
                    if self.policy.render_item_labels
                    else item.content
                )
                messages.append({"role": role, "content": content})
                manifest.append(item.id)
                manifest_entries.append(
                    RenderManifestEntry(
                        item_id=item.id,
                        source_ref=item.source_ref,
                        source_class=item.source_class,
                        role=role,
                        token_count=item.token_size,
                        application_key=item.application_key,
                    )
                )
                total_tokens += item.token_size
                class_totals[source_class] = class_totals.get(source_class, 0) + item.token_size
                rendered_items.append(item)

        for item in rendered_items:
            item.last_rendered_turn = current_turn

        self.log.append(
            turn=current_turn,
            kind=EventKind.rendered,
            item_id=None,
            payload={
                "manifest": list(manifest),
                "total_tokens": total_tokens,
                "class_totals": class_totals,
            },
        )

        return RenderResult(
            messages=messages,
            manifest=manifest,
            total_tokens=total_tokens,
            manifest_entries=manifest_entries,
        )
