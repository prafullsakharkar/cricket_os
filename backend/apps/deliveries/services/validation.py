"""Boundary validation for incoming delivery records (DATA_INGESTION §6).

Every rule below is applied BEFORE anything touches the database, and every
violation raises a typed :class:`DeliveryValidationError` (with a stable
``code`` the ingestion pipeline maps onto an ``IngestionError`` DLQ row with
``class=delivery_validation``). Nothing is silently coerced: an unknown enum,
an out-of-range run count or a contradictory wicket/retirement combination
fails the batch.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from apps.core.enums import DismissalType, RetirementType


class DeliveryValidationError(Exception):
    """A delivery record failed a boundary rule (DATA_INGESTION §6)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        delivery_seq: int | None = None,
        innings_id: Any = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.delivery_seq = delivery_seq
        self.innings_id = innings_id


@dataclass(frozen=True)
class DeliveryDraft:
    """One canonical delivery as produced by an adapter (internal UUIDs).

    Field names/values mirror the ``delivery`` columns; the service layer
    derives ``match_date`` from the match (never trusts the caller for it).
    ``revision`` is the provider/adapters' revision for this ball (1 for a
    fresh import; bumped by the adapter when provider content changed).
    """

    delivery_seq: int
    over_number: int
    ball_in_over: int
    bowler_id: Any
    batter_id: Any
    non_striker_id: Any
    runs_off_bat: int = 0
    wides: int = 0
    noballs: int = 0
    byes: int = 0
    leg_byes: int = 0
    penalty_runs: int = 0
    is_wicket: bool = False
    dismissal_type: str | None = None
    dismissed_player_id: Any = None
    bowler_credited_wicket: bool = False
    fielder1_id: Any = None
    fielder2_id: Any = None
    fielder3_id: Any = None
    shot_type: str | None = None
    commentary_text: str | None = None
    provider_delivery_id: str | None = None
    revision: int = 1
    retired_player_id: Any = None
    retirement_type: str | None = None

    def as_content_dict(self) -> dict[str, Any]:
        """The provider-visible content of this ball (excluding ``revision``,
        which identifies the content version rather than being part of it)."""
        return {
            "delivery_seq": self.delivery_seq,
            "over_number": self.over_number,
            "ball_in_over": self.ball_in_over,
            "bowler_id": str(self.bowler_id),
            "batter_id": str(self.batter_id),
            "non_striker_id": str(self.non_striker_id),
            "runs_off_bat": self.runs_off_bat,
            "wides": self.wides,
            "noballs": self.noballs,
            "byes": self.byes,
            "leg_byes": self.leg_byes,
            "penalty_runs": self.penalty_runs,
            "is_wicket": self.is_wicket,
            "dismissal_type": self.dismissal_type,
            "dismissed_player_id": None
            if self.dismissed_player_id is None
            else str(self.dismissed_player_id),
            "bowler_credited_wicket": self.bowler_credited_wicket,
            "fielder1_id": None if self.fielder1_id is None else str(self.fielder1_id),
            "fielder2_id": None if self.fielder2_id is None else str(self.fielder2_id),
            "fielder3_id": None if self.fielder3_id is None else str(self.fielder3_id),
            "shot_type": self.shot_type,
            "commentary_text": self.commentary_text,
            "provider_delivery_id": self.provider_delivery_id,
            "retired_player_id": None
            if self.retired_player_id is None
            else str(self.retired_player_id),
            "retirement_type": self.retirement_type,
        }


#: Allowed enum values, keyed by field name (unknown values are quarantined).
_ENUM_SETS: dict[str, Sequence[str]] = {
    "dismissal_type": DismissalType.values,
    "retirement_type": RetirementType.values,
}


def _fail(code: str, message: str, seq: int | None) -> None:
    raise DeliveryValidationError(code, message, delivery_seq=seq)


def validate_drafts(drafts: list[DeliveryDraft]) -> None:
    """Validate a batch of drafts (order-independent, all rules from §6).

    Raises :class:`DeliveryValidationError` on the first violation. ``seq``
    must be strictly increasing across the batch (gaps allowed, duplicates
    rejected) — this is the batch-level analogue of the per-innings monotonic
    ``delivery_seq`` invariant.
    """
    seen: set[int] = set()
    previous: int | None = None
    for draft in sorted(drafts, key=lambda d: d.delivery_seq):
        _validate_one(draft)
        if draft.delivery_seq in seen:
            _fail("duplicate_delivery_seq", "duplicate delivery_seq in batch", draft.delivery_seq)
        if previous is not None and draft.delivery_seq <= previous:
            _fail("out_of_order_seq", "delivery_seq not strictly increasing", draft.delivery_seq)
        seen.add(draft.delivery_seq)
        previous = draft.delivery_seq


def _validate_one(d: DeliveryDraft) -> None:
    seq = d.delivery_seq
    if d.delivery_seq < 1:
        _fail("invalid_seq", "delivery_seq must be >= 1", seq)
    if d.over_number < 1:
        _fail("invalid_over", "over_number must be >= 1", seq)
    if d.ball_in_over < 1:
        _fail("invalid_ball", "ball_in_over must be >= 1", seq)
    if not (0 <= d.runs_off_bat <= 6):
        _fail("runs_out_of_range", "runs_off_bat must be 0..6", seq)
    for name in ("wides", "noballs", "byes", "leg_byes", "penalty_runs"):
        if getattr(d, name) < 0:
            _fail("negative_extras", f"{name} must be >= 0", seq)

    # Wicket consistency (mirrors ck_delivery_wicket).
    if d.is_wicket:
        if d.dismissal_type is None or d.dismissed_player_id is None:
            _fail(
                "wicket_requires_dismissal",
                "a wicket requires dismissal_type and dismissed_player_id",
                seq,
            )
    elif d.dismissal_type is not None:
        _fail("dismissal_without_wicket", "dismissal_type without is_wicket", seq)

    # Enum containment.
    for name, allowed in _ENUM_SETS.items():
        value = getattr(d, name)
        if value is not None and value not in allowed:
            _fail("invalid_enum", f"unknown {name}: {value!r}", seq)

    # Retirement consistency (mirrors ck_delivery_retirement).
    if d.retired_player_id is None and d.retirement_type is not None:
        _fail("retirement_inconsistent", "retirement_type without retired_player_id", seq)
    if d.retirement_type is not None:
        if d.is_wicket:
            _fail(
                "retirement_inconsistent",
                "a HURT/NOT_OUT retirement cannot also be a wicket",
                seq,
            )
        if d.dismissal_type is not None:
            _fail(
                "retirement_inconsistent",
                "a HURT/NOT_OUT retirement never carries a dismissal_type",
                seq,
            )

    # RETIRED_OUT is a dismissal (adds a fall-of-wicket row): it requires
    # is_wicket, which the wicket-consistency rule above already enforces for
    # every dismissal_type (a dismissal without is_wicket -> dismissal_without_wicket).
