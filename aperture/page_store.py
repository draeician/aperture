"""PageStore: the non-destructive eviction target for paged items."""

from __future__ import annotations

from aperture.items import ContextItem, ItemState


class PageStore:
    """Holds all items in ItemState.paged, in eviction insertion order.

    Stores the ContextItem itself on insert (never a copy of it or its
    content), so recalled content is guaranteed byte-identical to what
    was evicted.
    """

    def __init__(self) -> None:
        self._order: list[int] = []
        self._items: dict[int, ContextItem] = {}

    def insert(self, item: ContextItem) -> None:
        if item.id in self._items:
            raise ValueError(f"duplicate item id: {item.id}")
        self._items[item.id] = item
        self._order.append(item.id)

    def remove(self, item_id: int) -> ContextItem:
        item = self._items.pop(item_id)
        self._order.remove(item_id)
        return item

    def __contains__(self, item_id: int) -> bool:
        return item_id in self._items

    def get(self, item_id: int) -> ContextItem:
        return self._items[item_id]

    def __iter__(self):
        for item_id in self._order:
            yield self._items[item_id]

    def __len__(self) -> int:
        return len(self._order)

    def check_invariants(self) -> None:
        ids = [item.id for item in self]
        assert len(ids) == len(set(ids)), "duplicate ids in PageStore"
        for item in self:
            assert item.state == ItemState.paged, (
                f"item {item.id} in PageStore has state {item.state!r}, expected paged"
            )
