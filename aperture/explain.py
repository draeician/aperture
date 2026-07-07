"""Explain: the read-only "why" API, derived from MutationLog + current state.

Attached to Aperture as kernel.explain (a namespace object, not loose
functions). Adds no auxiliary state of its own: every answer is computed
fresh, on demand, from the kernel's log and current containers.
"""

from __future__ import annotations

from aperture.errors import UnknownItemError
from aperture.items import ContextItem, EventKind, ItemState


def _is_restricted(item: ContextItem) -> bool:
    return bool(item.mneme_meta is not None and item.mneme_meta.get("render_restricted") is True)


def _event_dict(event) -> dict:
    return {
        "seq": event.seq,
        "turn": event.turn,
        "kind": event.kind,
        "item_id": event.item_id,
        "payload": dict(event.payload),
    }


class Explain:
    """Read-only explain namespace bound to one Aperture kernel instance."""

    def __init__(self, kernel) -> None:
        self._kernel = kernel

    def item(self, item_id: int) -> dict:
        self._kernel._check_open()

        events = self._kernel._log.events(item_id=item_id)
        if not events:
            raise UnknownItemError(f"unknown item id: {item_id}")

        if item_id in self._kernel._working_set:
            current_item = self._kernel._working_set.get(item_id)
        elif item_id in self._kernel._page_store:
            current_item = self._kernel._page_store.get(item_id)
        else:
            current_item = None

        if current_item is not None:
            current_turn = self._kernel._current_turn
            if current_item.ttl_turns is None:
                ttl_status = "no_ttl"
            elif current_turn - current_item.admitted_turn >= current_item.ttl_turns:
                ttl_status = "expired"
            else:
                ttl_status = "active"

            state = current_item.state
            priority = current_item.priority
            pinned = current_item.pinned
            ttl_turns = current_item.ttl_turns
        else:
            # Never admitted (rejected): no ContextItem ever existed.
            state = ItemState.rejected
            priority = None
            pinned = None
            ttl_turns = None
            ttl_status = None

        rendered_events = self._kernel._log.events(kind=EventKind.rendered)
        renders_appeared_in = [
            e.turn for e in rendered_events if item_id in e.payload.get("manifest", [])
        ]

        return {
            "item_id": item_id,
            "events": [_event_dict(e) for e in events],
            "state": state,
            "priority": priority,
            "pinned": pinned,
            "ttl_turns": ttl_turns,
            "ttl_status": ttl_status,
            "turns_present": sorted({e.turn for e in events}),
            "renders_appeared_in": renders_appeared_in,
        }

    def absence(self, item_id: int) -> dict:
        self._kernel._check_open()

        rejected_events = self._kernel._log.events(kind=EventKind.rejected, item_id=item_id)
        admitted_events = self._kernel._log.events(kind=EventKind.admitted, item_id=item_id)

        if not rejected_events and not admitted_events:
            raise UnknownItemError(f"unknown item id: {item_id}")

        if rejected_events:
            payload = dict(rejected_events[-1].payload)
            return {
                "reason": "rejected",
                "source_class": payload.get("source_class"),
                "token_size": payload.get("token_size"),
                "rejection_reason": payload.get("reason"),
                "turn": payload.get("turn"),
            }

        if item_id in self._kernel._page_store:
            evicted_events = self._kernel._log.events(kind=EventKind.evicted, item_id=item_id)
            last_eviction = evicted_events[-1].payload if evicted_events else {}
            return {
                "reason": "paged",
                "trigger": last_eviction.get("trigger"),
                "displaced_by": last_eviction.get("displaced_by"),
            }

        if item_id in self._kernel._working_set:
            item = self._kernel._working_set.get(item_id)
            if _is_restricted(item):
                return {"reason": "render_restricted"}
            return {"reason": "present"}

        raise UnknownItemError(f"unknown item id: {item_id}")

    def render(self, seq_or_last) -> dict:
        self._kernel._check_open()

        rendered_events = self._kernel._log.events(kind=EventKind.rendered)
        empty_result = {
            "found": False,
            "manifest": [],
            "class_totals": {},
            "diff": {"added": [], "removed": [], "recalled": []},
        }
        if not rendered_events:
            return empty_result

        if seq_or_last == "last":
            index = len(rendered_events) - 1
        else:
            matches = [i for i, e in enumerate(rendered_events) if e.seq == seq_or_last]
            if not matches:
                return empty_result
            index = matches[0]

        target = rendered_events[index]
        manifest = list(target.payload.get("manifest", []))
        class_totals = dict(target.payload.get("class_totals", {}))

        if index == 0:
            previous_manifest: list[int] = []
            window_start_turn = None
        else:
            previous = rendered_events[index - 1]
            previous_manifest = list(previous.payload.get("manifest", []))
            window_start_turn = previous.turn

        added = [i for i in manifest if i not in previous_manifest]
        removed = [i for i in previous_manifest if i not in manifest]

        recalled_ids = []
        if window_start_turn is not None:
            for event in self._kernel._log.events(kind=EventKind.recalled):
                if window_start_turn <= event.turn <= target.turn:
                    recalled_ids.append(event.item_id)

        return {
            "found": True,
            "manifest": manifest,
            "class_totals": class_totals,
            "diff": {"added": added, "removed": removed, "recalled": recalled_ids},
        }

    def budget(self) -> dict:
        self._kernel._check_open()

        budget = self._kernel._budget
        working_set = self._kernel._working_set
        policy = self._kernel._policy

        return {
            "effective_ceiling": budget.effective_ceiling(),
            "reserved": {
                "pinned": budget.pinned_renderable_total(),
                "index": self._kernel._page_index.cost(),
                "headroom": policy.reply_headroom,
            },
            "contested_pool": budget.contested_pool(),
            "class_totals": working_set.class_totals(),
            "restricted_nonrendered_tokens": working_set.restricted_total(),
        }

    def log(self, kind=None, item_id=None) -> list[dict]:
        self._kernel._check_open()
        events = self._kernel._log.events(kind=kind, item_id=item_id)
        return [_event_dict(e) for e in events]

    def starved(self) -> list[int]:
        self._kernel._check_open()
        current_turn = self._kernel._current_turn

        result = []
        for item in self._kernel._working_set:
            if _is_restricted(item):
                continue
            if item.last_rendered_turn is not None:
                continue
            if current_turn - item.admitted_turn >= 2:
                result.append(item.id)
        return result
