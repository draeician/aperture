"""Aperture: the Phase 0 facade wiring governance, budget, page index,
renderer, and explain into a single session-scoped object.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aperture.budget import BalanceReport, BudgetGovernor
from aperture.errors import (
    ExpiredRecallError,
    InvalidItemError,
    SessionClosedError,
    UnknownItemError,
)
from aperture.explain import Explain
from aperture.governance import (
    dedup_tool_output,
    redact,
    register_tool_output_hash,
    truncate_tool_output,
)
from aperture.items import ContextItem, EventKind, ItemState, SourceClass, Submission
from aperture.log import MutationLog
from aperture.page_index import PageIndex, mechanical_index_line
from aperture.page_store import PageStore
from aperture.policy import Policy
from aperture.renderer import RenderResult, Renderer
from aperture.tokenizer import get_tokenizer
from aperture.working_set import WorkingSet


@dataclass(frozen=True)
class AdmissionResult:
    item_id: int
    accepted: bool
    content: str
    reason: str | None = None


@dataclass(frozen=True)
class SessionExport:
    working_set: list[dict]
    page_store: list[dict]
    log: list[dict]


class Aperture:
    """Session-scoped context governor facade. One instance per session;
    instances share no state (no module-level mutable state anywhere)."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy
        self._tokenizer = get_tokenizer(policy.tokenizer_id)

        self._working_set = WorkingSet()
        self._page_store = PageStore()
        self._log = MutationLog()
        self._tool_output_hashes: dict[str, int] = {}

        self._id_counter = 0
        self._current_turn = 0
        self._closed = False

        self._page_index = PageIndex(
            page_store=self._page_store,
            policy=policy,
            tokenizer=self._tokenizer,
            log=self._log,
        )
        self._budget = BudgetGovernor(
            policy=policy,
            working_set=self._working_set,
            page_store=self._page_store,
            log=self._log,
            page_index_cost=self._page_index.cost,
        )
        self._renderer = Renderer(
            working_set=self._working_set,
            policy=policy,
            page_index=self._page_index,
            log=self._log,
            is_balanced=self._is_balanced,
        )
        self.explain = Explain(self)

    # --- internal helpers -----------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise SessionClosedError("session has ended")

    def _assign_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _get_item(self, item_id: int) -> ContextItem:
        if item_id in self._working_set:
            return self._working_set.get(item_id)
        if item_id in self._page_store:
            return self._page_store.get(item_id)
        raise UnknownItemError(f"unknown item id: {item_id}")

    def _is_balanced(self) -> bool:
        pinned_total = self._budget.pinned_renderable_total()
        ceiling = self._budget.effective_ceiling()
        if pinned_total + self._policy.reply_headroom > ceiling:
            return False

        contested_pool = self._budget.contested_pool()
        if self._working_set.renderable_total(unpinned_only=True) > contested_pool:
            return False

        class_totals = self._working_set.class_totals(unpinned_only=True)
        for source_class, fraction in self._policy.class_subbudgets.items():
            cap = math.floor(fraction * contested_pool)
            if class_totals.get(source_class, 0) > cap:
                return False

        return True

    @staticmethod
    def _item_to_dict(item: ContextItem) -> dict:
        return {
            "id": item.id,
            "source_class": item.source_class,
            "content": item.content,
            "token_size": item.token_size,
            "priority": item.priority,
            "pinned": item.pinned,
            "ttl_turns": item.ttl_turns,
            "provenance": {
                "submitted_by": item.provenance.submitted_by,
                "origin": item.provenance.origin,
                "mneme_record_id": item.provenance.mneme_record_id,
            },
            "index_line": item.index_line,
            "state": item.state,
            "admitted_turn": item.admitted_turn,
            "last_rendered_turn": item.last_rendered_turn,
            "mneme_meta": dict(item.mneme_meta) if item.mneme_meta is not None else None,
        }

    # --- submit -----------------------------------------------------------

    def submit(self, submission: Submission) -> AdmissionResult:
        self._check_open()

        if submission.source_class == SourceClass.page_index:
            raise InvalidItemError("source_class=page_index is reserved for the kernel")
        if submission.content == "":
            raise InvalidItemError("content must not be empty")
        if submission.ttl_turns is not None and submission.ttl_turns < 0:
            raise InvalidItemError("ttl_turns must not be negative")

        item_id = self._assign_id()

        content, index_line = redact(
            content=submission.content,
            index_line=submission.index_line,
            rules=self._policy.redaction_rules,
            log=self._log,
            item_id=item_id,
            turn=self._current_turn,
        )
        content = truncate_tool_output(
            source_class=submission.source_class,
            content=content,
            structure=submission.structure,
            max_tokens=self._policy.tool_output_max_tokens,
            mode=self._policy.tool_truncation,
            tokenizer=self._tokenizer,
            log=self._log,
            item_id=item_id,
            turn=self._current_turn,
        )
        content = dedup_tool_output(
            source_class=submission.source_class,
            content=content,
            hash_registry=self._tool_output_hashes,
            item_id=item_id,
            log=self._log,
            turn=self._current_turn,
        )

        token_size = self._tokenizer.count(content)

        contested_pool = self._budget.contested_pool()
        if token_size > contested_pool:
            reason = f"token_size {token_size} exceeds contested_pool {contested_pool}"
            self._log.append(
                turn=self._current_turn,
                kind=EventKind.rejected,
                item_id=item_id,
                payload={
                    "source_class": submission.source_class,
                    "token_size": token_size,
                    "reason": reason,
                    "turn": self._current_turn,
                },
            )
            return AdmissionResult(item_id=item_id, accepted=False, content=content, reason=reason)

        priority = (
            submission.priority
            if submission.priority is not None
            else self._policy.class_defaults.get(submission.source_class, 0)
        )
        final_index_line = index_line if index_line is not None else mechanical_index_line(content)

        item = ContextItem(
            id=item_id,
            source_class=submission.source_class,
            content=content,
            token_size=token_size,
            priority=priority,
            pinned=submission.pinned,
            ttl_turns=submission.ttl_turns,
            provenance=submission.provenance,
            index_line=final_index_line,
            state=ItemState.working,
            admitted_turn=self._current_turn,
            last_rendered_turn=None,
            mneme_meta=submission.mneme_meta,
        )
        self._working_set.insert(item)

        if submission.source_class == SourceClass.tool_output:
            register_tool_output_hash(self._tool_output_hashes, content, item_id)

        self._log.append(
            turn=self._current_turn,
            kind=EventKind.admitted,
            item_id=item_id,
            payload={"source_class": submission.source_class, "token_size": token_size},
        )

        return AdmissionResult(item_id=item_id, accepted=True, content=content, reason=None)

    # --- balance / render ------------------------------------------------

    def balance(self) -> BalanceReport:
        self._check_open()
        report = self._budget.balance(self._current_turn)
        self._page_index.sync_collapse_log(self._current_turn)
        return report

    def render(self) -> RenderResult:
        self._check_open()
        return self._renderer.render(self._current_turn)

    # --- recall / pin / ttl -----------------------------------------------

    def recall(self, item_id: int) -> BalanceReport:
        self._check_open()

        if item_id in self._working_set:
            return BalanceReport(evicted_ids=[], triggers={}, displaced_by={}, changed=False)

        if item_id not in self._page_store:
            raise UnknownItemError(f"unknown item id: {item_id}")

        item = self._page_store.get(item_id)
        if item.ttl_turns is not None and self._current_turn - item.admitted_turn >= item.ttl_turns:
            raise ExpiredRecallError(item_id, item.admitted_turn + item.ttl_turns)

        self._page_store.remove(item_id)
        item.state = ItemState.working
        self._working_set.insert(item)

        self._log.append(
            turn=self._current_turn,
            kind=EventKind.recalled,
            item_id=item_id,
            payload={},
        )

        return self._budget.balance(
            self._current_turn, trigger_override="recall", displaced_by=item_id
        )

    def pin(self, item_id: int, pinned: bool) -> None:
        self._check_open()
        item = self._get_item(item_id)
        item.pinned = pinned

    def extend_ttl(self, item_id: int, turns: int) -> None:
        self._check_open()
        if turns <= 0:
            raise InvalidItemError("turns must be positive")

        item = self._get_item(item_id)
        old_ttl = item.ttl_turns
        new_ttl = (old_ttl or 0) + turns
        item.ttl_turns = new_ttl

        self._log.append(
            turn=self._current_turn,
            kind=EventKind.ttl_extended,
            item_id=item_id,
            payload={"old_ttl": old_ttl, "new_ttl": new_ttl},
        )

    def next_turn(self) -> int:
        self._check_open()
        self._current_turn += 1
        return self._current_turn

    # --- agent-driven recall -----------------------------------------------

    def recall_request_schema(self) -> dict:
        self._check_open()
        return {"name": "aperture_recall", "input": {"item_id": int}}

    def handle_recall_request(self, payload: dict) -> str:
        self._check_open()

        if not isinstance(payload, dict) or not isinstance(payload.get("item_id"), int):
            return "error: payload must be {'item_id': <int>}"

        item_id = payload["item_id"]
        try:
            self.recall(item_id)
        except UnknownItemError:
            return f"error: unknown item id {item_id}"
        except ExpiredRecallError as exc:
            return f"error: item {item_id} is expired (expiry turn {exc.expiry_turn})"

        return f"recalled item {item_id}"

    # --- session lifecycle -------------------------------------------------

    def end_session(self) -> SessionExport:
        self._check_open()

        self._log.append(
            turn=self._current_turn,
            kind=EventKind.session_export,
            item_id=None,
            payload={},
        )

        export = SessionExport(
            working_set=[self._item_to_dict(item) for item in self._working_set],
            page_store=[self._item_to_dict(item) for item in self._page_store],
            log=self._log.export(),
        )

        self._closed = True
        self._working_set = WorkingSet()
        self._page_store = PageStore()
        self._log = MutationLog()
        self._tool_output_hashes = {}

        return export
