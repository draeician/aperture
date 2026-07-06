"""Aperture error types."""

from __future__ import annotations


class ApertureError(Exception):
    """Base class for all aperture errors."""


class PolicyError(ApertureError):
    """Raised when a Policy fails construction-time validation."""


class InvalidItemError(ApertureError):
    """Raised for a bad submission or a non-positive extend_ttl call."""


class PinOverflowError(ApertureError):
    """Raised when pinned renderable total + headroom exceed the effective ceiling."""

    def __init__(self, pinned_total: int, ceiling: int) -> None:
        self.pinned_total = pinned_total
        self.ceiling = ceiling
        super().__init__(
            f"pinned total {pinned_total} exceeds effective ceiling {ceiling}"
        )


class UnbalancedError(ApertureError):
    """Raised when render() is called while budget invariants do not hold."""


class UnknownItemError(ApertureError):
    """Raised when an item id was never assigned, or is not in the expected store."""


class ExpiredRecallError(ApertureError):
    """Raised when recall() is attempted on an item flagged expired."""

    def __init__(self, item_id: int, expiry_turn: int) -> None:
        self.item_id = item_id
        self.expiry_turn = expiry_turn
        super().__init__(f"item {item_id} expired at turn {expiry_turn}")


class SessionClosedError(ApertureError):
    """Raised for any call made after end_session()."""
