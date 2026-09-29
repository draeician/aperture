"""WorkingSet: the container of currently-admitted, render-eligible items."""

from __future__ import annotations

from aperture.items import ContextItem, ItemState, SourceClass, is_render_restricted


class WorkingSet:
    """Holds all items in ItemState.working, keyed by id.

    Iteration order is always ascending id. Render-restricted items are excluded from every
    token-total query; they remain iterable/membership-visible.
    """

    def __init__(self) -> None:
        self._items: dict[int, ContextItem] = {}

    def insert(self, item: ContextItem) -> None:
        if item.id in self._items:
            raise ValueError(f"duplicate item id: {item.id}")
        self._items[item.id] = item

    def __contains__(self, item_id: int) -> bool:
        return item_id in self._items

    def get(self, item_id: int) -> ContextItem:
        return self._items[item_id]

    def remove(self, item_id: int) -> ContextItem:
        return self._items.pop(item_id)

    def __iter__(self):
        for item_id in sorted(self._items):
            yield self._items[item_id]

    def __len__(self) -> int:
        return len(self._items)

    def class_totals(self, *, unpinned_only: bool = False) -> dict[SourceClass, int]:
        """Per-class token totals for renderable (non-restricted) items.

        If unpinned_only, pinned items are excluded as well.
        """
        totals: dict[SourceClass, int] = {}
        for item in self:
            if is_render_restricted(item):
                continue
            if unpinned_only and item.pinned:
                continue
            totals[item.source_class] = totals.get(item.source_class, 0) + item.token_size
        return totals

    def renderable_total(self, *, unpinned_only: bool = False) -> int:
        """Total token_size across classes for renderable (non-restricted) items."""
        return sum(self.class_totals(unpinned_only=unpinned_only).values())

    def restricted_total(self) -> int:
        """Total token_size across classes for restricted items."""
        return sum(item.token_size for item in self if is_render_restricted(item))

    def check_invariants(self) -> None:
        ids = [item.id for item in self]
        assert len(ids) == len(set(ids)), "duplicate ids in WorkingSet"
        for item in self:
            assert item.state == ItemState.working, (
                f"item {item.id} in WorkingSet has state {item.state!r}, expected working"
            )
