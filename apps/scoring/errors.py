"""Typed, boundary-level errors for the pure scoring engine.

Every input problem is rejected at the boundary with a :class:`ScoringInputError`
carrying the offending ``delivery_seq`` (or innings/event identity) so callers can
locate the exact row. Values are never silently coerced or dropped.
"""

from __future__ import annotations

from typing import Any


class ScoringError(Exception):
    """Base class for every scoring failure."""


class ScoringInputError(ScoringError, ValueError):
    """An input row or context is invalid.

    ``delivery_seq`` and ``innings_id`` identify the offending row when known.
    """

    def __init__(
        self,
        message: str,
        *,
        delivery_seq: int | None = None,
        innings_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.delivery_seq = delivery_seq
        self.innings_id = innings_id
        self.details = details or {}
        location = []
        if innings_id is not None:
            location.append(f"innings={innings_id}")
        if delivery_seq is not None:
            location.append(f"delivery_seq={delivery_seq}")
        suffix = f" ({', '.join(location)})" if location else ""
        super().__init__(f"{message}{suffix}")


class InvalidDeliveryError(ScoringInputError):
    """A delivery violates a structural or range constraint."""


class DuplicateDeliveryError(ScoringInputError):
    """Two current rows share the same ``(innings_id, delivery_seq)``."""


class OutOfOrderDeliveryError(ScoringInputError):
    """Deliveries cannot be ordered deterministically by ``delivery_seq``."""


class SupersededDataError(ScoringInputError):
    """A non-current (superseded) row was supplied; only current rows are valid."""


class InvalidInningsError(ScoringInputError):
    """Innings metadata is inconsistent with its rows."""


class InvalidEventError(ScoringInputError):
    """An innings event is malformed or out of order."""


class DataQualityError(ScoringInputError):
    """Rows are individually valid but contradict each other."""


class RetirementSourceConflictError(DataQualityError):
    """The same retirement is recorded on both a delivery and an innings event."""


class InvalidStatInputError(ScoringError, ValueError):
    """Aggregate inputs are logically impossible (e.g. not-outs > innings)."""


class MissingDlsTargetError(ScoringInputError):
    """A DLS match has no provider-supplied target."""


class LicensedMethodRequired(ScoringError):
    """A licensed method (DLS) would be needed; the platform never computes it."""

    def __init__(self, method: str = "DLS") -> None:
        self.method = method
        super().__init__(
            f"{method} is a licensed method: supply the provider/licensed value "
            "instead of computing it"
        )
