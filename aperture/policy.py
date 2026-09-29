"""Policy and RedactionRule, with construction-time validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from aperture.errors import PolicyError
from aperture.items import SourceClass, TruncationMode

_TIKTOKEN_PREFIX = "tiktoken" + ":"


@dataclass(frozen=True)
class RedactionRule:
    rule_id: str
    pattern: str
    label: str


@dataclass(frozen=True)
class Policy:
    budget_total: int = 8000
    reply_headroom: int = 0
    page_index_budget: int = 500
    class_defaults: Mapping[SourceClass, int] = field(default_factory=dict)
    class_subbudgets: Mapping[SourceClass, float] = field(default_factory=dict)
    class_eviction_order: tuple[SourceClass, ...] = (
        SourceClass.scratch,
        SourceClass.tool_output,
        SourceClass.conversation,
        SourceClass.memory,
        SourceClass.mneme_import,
        SourceClass.user_fact,
    )
    tool_output_max_tokens: int = 2000
    tool_truncation: TruncationMode = TruncationMode.head_tail
    redaction_rules: tuple[RedactionRule, ...] = ()
    render_order: tuple[SourceClass, ...] = (
        SourceClass.system,
        SourceClass.user_fact,
        SourceClass.conversation,
        SourceClass.tool_output,
        SourceClass.scratch,
        SourceClass.memory,
        SourceClass.mneme_import,
        SourceClass.page_index,
    )
    tokenizer_id: str = "whitespace"
    token_safety_margin: float = 0.0
    render_item_labels: bool = True
    policy_id: str | None = None
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if self.budget_total <= 0:
            raise PolicyError("budget_total must be > 0")

        for source_class, fraction in self.class_subbudgets.items():
            if not (0 < fraction <= 1):
                raise PolicyError(
                    f"class_subbudgets[{source_class}] must be in (0, 1], got {fraction}"
                )

        if SourceClass.system in self.class_eviction_order:
            raise PolicyError("system must not appear in class_eviction_order")

        if not self.render_order or self.render_order[0] != SourceClass.system:
            raise PolicyError("render_order must start with system")

        if SourceClass.page_index not in self.render_order:
            raise PolicyError("render_order must include page_index")

        for rule in self.redaction_rules:
            try:
                re.compile(rule.pattern)
            except re.error as exc:
                raise PolicyError(
                    f"redaction rule {rule.rule_id!r} has invalid pattern: {exc}"
                ) from exc

        if self.tokenizer_id != "whitespace" and not (
            self.tokenizer_id.startswith(_TIKTOKEN_PREFIX)
            and len(self.tokenizer_id) > len(_TIKTOKEN_PREFIX)
        ):
            raise PolicyError(
                f"tokenizer_id must be 'whitespace' or '{_TIKTOKEN_PREFIX}<encoding>', got {self.tokenizer_id!r}"
            )
