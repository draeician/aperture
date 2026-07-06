"""PageIndex: the rendered summary of PageStore contents.

Renders a fixed text block (header, one entry per paged item in
eviction order, collapsing the oldest entries into a single footer
once the rendered cost exceeds policy.page_index_budget) and accounts
its token cost with the session tokenizer. Never mutates PageStore;
collapsed items remain fully present there. Logging (index_collapsed)
is a separate, explicit step (sync_collapse_log) so that cost()/
render() stay pure and safe to call repeatedly (e.g. as
BudgetGovernor's injected page_index_cost provider) without producing
duplicate log entries.
"""

from __future__ import annotations

from aperture.items import ContextItem, EventKind
from aperture.log import MutationLog
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.tokenizer import Tokenizer

HEADER = "--- PAGED CONTEXT (recall by id) ---"


def _is_restricted(item: ContextItem) -> bool:
    return bool(item.mneme_meta is not None and item.mneme_meta.get("render_restricted") is True)


def mechanical_index_line(content: str) -> str:
    """First 60 characters of content with newlines collapsed to spaces.

    A hard cut with no other processing; never generated from meaning.
    """
    return content.replace("\n", " ")[:60]


class PageIndex:
    """Builds the rendered page-index block from PageStore + Policy."""

    HEADER = HEADER

    def __init__(
        self,
        *,
        page_store: PageStore,
        policy: Policy,
        tokenizer: Tokenizer,
        log: MutationLog,
    ) -> None:
        self.page_store = page_store
        self.policy = policy
        self.tokenizer = tokenizer
        self.log = log
        self._logged_collapsed_ids: set[int] = set()

    def render(self) -> str:
        """The current rendered block, or '' if there are no paged items."""
        text, _collapsed_ids = self._build()
        return text

    def cost(self) -> int:
        """Token cost of the current rendered block (0 if no paged items)."""
        if len(self.page_store) == 0:
            return 0
        text, _collapsed_ids = self._build()
        return self.tokenizer.count(text)

    def collapsed_ids(self) -> list[int]:
        """Ids currently folded into the collapse footer (empty if none)."""
        _text, collapsed_ids = self._build()
        return collapsed_ids

    def sync_collapse_log(self, turn: int) -> None:
        """Log index_collapsed for any newly-collapsed ids since the last call.

        Idempotent: if the current collapse set has not grown, logs
        nothing.
        """
        _text, collapsed_ids = self._build()
        new_ids = sorted(set(collapsed_ids) - self._logged_collapsed_ids)
        if not new_ids:
            return
        self.log.append(
            turn=turn,
            kind=EventKind.index_collapsed,
            item_id=None,
            payload={"collapsed_ids": new_ids},
        )
        self._logged_collapsed_ids.update(new_ids)

    def _build(self) -> tuple[str, list[int]]:
        paged_items = list(self.page_store)  # eviction order, oldest first
        if not paged_items:
            return "", []

        collapse_count = 0
        while True:
            collapsed_ids = [item.id for item in paged_items[:collapse_count]]
            lines = [HEADER]
            if collapsed_ids:
                lines.append(self._footer_line(collapsed_ids))
            lines.extend(self._entry_line(item) for item in paged_items[collapse_count:])
            text = "\n".join(lines)

            if self.tokenizer.count(text) <= self.policy.page_index_budget:
                return text, collapsed_ids
            if collapse_count >= len(paged_items):
                return text, collapsed_ids
            collapse_count += 1

    @staticmethod
    def _footer_line(collapsed_ids: list[int]) -> str:
        return (
            f"+ {len(collapsed_ids)} older paged items "
            f"(ids {min(collapsed_ids)}..{max(collapsed_ids)}; use explain)"
        )

    @staticmethod
    def _entry_line(item: ContextItem) -> str:
        if _is_restricted(item):
            return f"#{item.id} [mneme_import] (restricted) ({item.token_size} tok)"
        return f"#{item.id} [{item.source_class}] {item.index_line} ({item.token_size} tok)"
