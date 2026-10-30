"""Boundary validation and deterministic ordering of canonical rows."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import pairwise

from apps.scoring.errors import (
    DataQualityError,
    DuplicateDeliveryError,
    InvalidEventError,
    OutOfOrderDeliveryError,
    SupersededDataError,
)
from apps.scoring.types import DeliveryRecord, InningsEvent


def ordered_deliveries(
    deliveries: Iterable[DeliveryRecord], *, innings_id: str | None = None
) -> tuple[DeliveryRecord, ...]:
    """Reject superseded/duplicate rows and return ``delivery_seq`` order.

    ``delivery_seq`` is authoritative (DATABASE_SCHEMA §3.6); input order is
    never trusted.
    """

    rows = tuple(deliveries)
    seen: set[tuple[str, int]] = set()
    for delivery in rows:
        if not delivery.is_current:
            raise SupersededDataError(
                "superseded delivery row supplied; only is_current rows are valid",
                delivery_seq=delivery.delivery_seq,
                innings_id=delivery.innings_id,
            )
        key = (delivery.innings_id, delivery.delivery_seq)
        if key in seen:
            raise DuplicateDeliveryError(
                "duplicate current (innings_id, delivery_seq)",
                delivery_seq=delivery.delivery_seq,
                innings_id=delivery.innings_id,
            )
        seen.add(key)
        if innings_id is not None and delivery.innings_id != innings_id:
            raise DataQualityError(
                "delivery belongs to a different innings",
                delivery_seq=delivery.delivery_seq,
                innings_id=delivery.innings_id,
            )
    return tuple(sorted(rows, key=lambda row: row.delivery_seq))


def ordered_events(
    events: Iterable[InningsEvent], *, innings_id: str | None = None
) -> tuple[InningsEvent, ...]:
    rows = tuple(events)
    seen: set[tuple[str, str, str | None, int | None]] = set()
    for event in rows:
        if not event.is_current:
            raise SupersededDataError(
                "superseded innings event supplied",
                delivery_seq=event.anchor_delivery_seq,
                innings_id=event.innings_id,
            )
        key = (event.innings_id, event.event_type, event.player_id, event.anchor_delivery_seq)
        if key in seen:
            raise DataQualityError(
                "duplicate current innings event identity",
                delivery_seq=event.anchor_delivery_seq,
                innings_id=event.innings_id,
            )
        seen.add(key)
        if innings_id is not None and event.innings_id != innings_id:
            raise DataQualityError(
                "event belongs to a different innings",
                delivery_seq=event.anchor_delivery_seq,
                innings_id=event.innings_id,
            )
    return tuple(
        sorted(
            rows,
            key=lambda event: (
                -1 if event.anchor_delivery_seq is None else event.anchor_delivery_seq,
                event.event_type,
                event.player_id or "",
            ),
        )
    )


def validate_anchor_sequences(
    events: Sequence[InningsEvent], deliveries: Sequence[DeliveryRecord]
) -> None:
    sequences = {delivery.delivery_seq for delivery in deliveries}
    for event in events:
        if event.anchor_delivery_seq is not None and event.anchor_delivery_seq not in sequences:
            raise InvalidEventError(
                "event anchor_delivery_seq does not reference a current delivery",
                delivery_seq=event.anchor_delivery_seq,
                innings_id=event.innings_id,
            )


def ensure_strictly_increasing(deliveries: Sequence[DeliveryRecord]) -> None:
    for previous, current in pairwise(deliveries):
        if current.delivery_seq <= previous.delivery_seq:
            raise OutOfOrderDeliveryError(
                "delivery_seq must be strictly increasing",
                delivery_seq=current.delivery_seq,
                innings_id=current.innings_id,
            )
