"""Aperture: a deterministic, in-process context governor for LLM applications.

Public API surface, including explain.* (accessed as Aperture(...).explain).
"""

from __future__ import annotations

from aperture.budget import BalanceReport
from aperture.errors import (
    ApertureError,
    ExpiredRecallError,
    InvalidItemError,
    PinOverflowError,
    PolicyError,
    SessionClosedError,
    UnbalancedError,
    UnknownItemError,
)
from aperture.explain import Explain
from aperture.items import (
    ContextItem,
    EventKind,
    ItemState,
    Provenance,
    SourceClass,
    Structure,
    Submission,
    TruncationMode,
)
from aperture.kernel import AdmissionResult, Aperture, SessionExport
from aperture.policy import Policy, RedactionRule
from aperture.renderer import RenderResult

__all__ = [
    "Aperture",
    "Policy",
    "RedactionRule",
    "Submission",
    "Provenance",
    "ContextItem",
    "SourceClass",
    "ItemState",
    "Structure",
    "TruncationMode",
    "EventKind",
    "AdmissionResult",
    "BalanceReport",
    "RenderResult",
    "SessionExport",
    "Explain",
    "ApertureError",
    "PolicyError",
    "InvalidItemError",
    "PinOverflowError",
    "UnbalancedError",
    "UnknownItemError",
    "ExpiredRecallError",
    "SessionClosedError",
]
