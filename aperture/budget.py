"""BudgetGovernor: budget figures and balance() (the only evicting operation).

page_index.py is a later step; page_index_cost here is an injectable
zero-arg provider so BudgetGovernor can be built and tested in
isolation. on_evict is an optional hook invoked after each eviction so
a later step (PageIndex) can react without BudgetGovernor depending on
it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from aperture.errors import PinOverflowError
from aperture.items import ContextItem, EventKind, ItemState, SourceClass, is_render_restricted
from aperture.log import MutationLog
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.working_set import WorkingSet


@dataclass(frozen=True)
class BalanceReport:
    evicted_ids: list[int]
    triggers: dict[int, str]
    displaced_by: dict[int, int | None]
    changed: bool


class BudgetGovernor:
    """Computes effective_ceiling/reserved/contested_pool and runs balance()."""

    def __init__(
        self,
        *,
        policy: Policy,
        working_set: WorkingSet,
        page_store: PageStore,
        log: MutationLog,
        page_index_cost: Callable[[], int],
        on_evict: Callable[[ContextItem], None] | None = None,
    ) -> None:
        self.policy = policy
        self.working_set = working_set
        self.page_store = page_store
        self.log = log
        self._page_index_cost = page_index_cost
        self._on_evict = on_evict
        self._expired_flagged_ids: set[int] = set()
        self._current_turn = 0
        self._trigger_override: str | None = None
        self._displaced_by_override: int | None = None

    # --- budget figures ------------------------------------------------

    def effective_ceiling(self) -> int:
        return math.floor(self.policy.budget_total * (1 - self.policy.token_safety_margin))

    def pinned_renderable_total(self) -> int:
        return self.working_set.renderable_total() - self.working_set.renderable_total(
            unpinned_only=True
        )

    def reserved(self) -> int:
        return (
            self.pinned_renderable_total() + self._page_index_cost() + self.policy.reply_headroom
        )

    def contested_pool(self) -> int:
        return self.effective_ceiling() - self.reserved()

    def _is_over_budget(self) -> bool:
        return self.working_set.renderable_total(unpinned_only=True) > self.contested_pool()

    # --- balance / eviction ---------------------------------------------

    def balance(
        self,
        current_turn: int,
        *,
        trigger_override: str | None = None,
        displaced_by: int | None = None,
    ) -> BalanceReport:
        """Run the Eviction Algorithm until budget invariants hold (or no
        more eligible candidates exist). Raises PinOverflowError (logging
        nothing, evicting nothing) if pinned content alone cannot fit.
        Idempotent: with no intervening mutation, a repeat call evicts
        and logs nothing.

        trigger_override/displaced_by let a caller (recall()) tag every
        eviction this specific call makes as trigger="recall" with
        displaced_by set to the recalling item's id, instead of the
        pass-determined "ttl"/"subbudget"/"global". Omitted (the
        default), behavior is unchanged from a plain balance() call.
        """
        self._current_turn = current_turn
        self._trigger_override = trigger_override
        self._displaced_by_override = displaced_by

        pinned_total = self.pinned_renderable_total()
        ceiling = self.effective_ceiling()
        if pinned_total + self.policy.reply_headroom > ceiling:
            raise PinOverflowError(pinned_total, ceiling)

        self._flag_expired(current_turn)

        evicted_ids: list[int] = []
        triggers: dict[int, str] = {}
        displaced_by: dict[int, int | None] = {}

        self._pass_a_ttl(current_turn, evicted_ids, triggers, displaced_by)
        self._pass_b_subbudgets(evicted_ids, triggers, displaced_by)
        self._pass_c_global(evicted_ids, triggers, displaced_by)

        return BalanceReport(
            evicted_ids=evicted_ids,
            triggers=triggers,
            displaced_by=displaced_by,
            changed=bool(evicted_ids),
        )

    def _flag_expired(self, current_turn: int) -> None:
        for item in list(self.working_set):
            if item.ttl_turns is None:
                continue
            if current_turn - item.admitted_turn < item.ttl_turns:
                continue
            if item.id in self._expired_flagged_ids:
                continue
            self._expired_flagged_ids.add(item.id)
            self.log.append(
                turn=current_turn,
                kind=EventKind.expired_flagged,
                item_id=item.id,
                payload={},
            )

    def _expired_unpinned_candidates(self, current_turn: int) -> list[ContextItem]:
        return [
            item
            for item in self.working_set
            if not item.pinned
            and item.source_class != SourceClass.system
            and item.ttl_turns is not None
            and current_turn - item.admitted_turn >= item.ttl_turns
        ]

    def _eligible_candidates(self, source_class: SourceClass | None = None) -> list[ContextItem]:
        result = []
        for item in self.working_set:
            if item.pinned or item.source_class == SourceClass.system:
                continue
            if is_render_restricted(item):
                continue
            if source_class is not None and item.source_class != source_class:
                continue
            result.append(item)
        return result

    @staticmethod
    def _tie_break_key(item: ContextItem):
        last_rendered = item.last_rendered_turn if item.last_rendered_turn is not None else -1
        return (item.priority, -item.token_size, last_rendered, item.id)

    def _pass_a_ttl(
        self,
        current_turn: int,
        evicted_ids: list[int],
        triggers: dict[int, str],
        displaced_by: dict[int, int | None],
    ) -> None:
        while self._is_over_budget():
            candidates = self._expired_unpinned_candidates(current_turn)
            if not candidates:
                break
            candidates.sort(key=lambda item: (item.admitted_turn, item.id))
            victim = candidates[0]
            self._evict(victim, "ttl", None, evicted_ids, triggers, displaced_by)

    def _effective_eviction_order(self) -> list[SourceClass]:
        order = list(self.policy.class_eviction_order)
        if SourceClass.memory not in order:
            if SourceClass.mneme_import in order:
                order.insert(order.index(SourceClass.mneme_import), SourceClass.memory)
            else:
                order.append(SourceClass.memory)
        return order

    def _pass_b_subbudgets(
        self,
        evicted_ids: list[int],
        triggers: dict[int, str],
        displaced_by: dict[int, int | None],
    ) -> None:
        for source_class in self._effective_eviction_order():
            fraction = self.policy.class_subbudgets.get(source_class)
            if fraction is None:
                continue
            while True:
                cap = math.floor(fraction * self.contested_pool())
                class_total = self.working_set.class_totals(unpinned_only=True).get(
                    source_class, 0
                )
                if class_total <= cap:
                    break
                candidates = self._eligible_candidates(source_class)
                if not candidates:
                    break
                victim = min(candidates, key=self._tie_break_key)
                self._evict(victim, "subbudget", None, evicted_ids, triggers, displaced_by)

    def _pass_c_global(
        self,
        evicted_ids: list[int],
        triggers: dict[int, str],
        displaced_by: dict[int, int | None],
    ) -> None:
        class_order = self._effective_eviction_order()
        idx = 0
        while self._is_over_budget():
            if idx >= len(class_order):
                break
            candidates = self._eligible_candidates(class_order[idx])
            if not candidates:
                idx += 1
                continue
            victim = min(candidates, key=self._tie_break_key)
            self._evict(victim, "global", None, evicted_ids, triggers, displaced_by)

    def _evict(
        self,
        item: ContextItem,
        trigger: str,
        displaced_by_id: int | None,
        evicted_ids: list[int],
        triggers: dict[int, str],
        displaced_by: dict[int, int | None],
    ) -> None:
        if self._trigger_override is not None:
            trigger = self._trigger_override
            displaced_by_id = self._displaced_by_override

        self.working_set.remove(item.id)
        item.state = ItemState.paged
        self.page_store.insert(item)

        evicted_ids.append(item.id)
        triggers[item.id] = trigger
        displaced_by[item.id] = displaced_by_id

        self.log.append(
            turn=self._current_turn,
            kind=EventKind.evicted,
            item_id=item.id,
            payload={"trigger": trigger, "displaced_by": displaced_by_id},
        )

        if self._on_evict is not None:
            self._on_evict(item)
