"""Boundary ordering/validation helpers."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import INNINGS, delivery, event, legal_run

from apps.scoring.errors import (
    DataQualityError,
    DuplicateDeliveryError,
    InvalidEventError,
    OutOfOrderDeliveryError,
    SupersededDataError,
)
from apps.scoring.types import InningsEventType
from apps.scoring.validation import (
    ensure_strictly_increasing,
    ordered_deliveries,
    ordered_events,
    validate_anchor_sequences,
)


def test_ordered_deliveries_sorts_and_keeps_only_current() -> None:
    rows = ordered_deliveries([legal_run(2), legal_run(1)])
    assert [row.delivery_seq for row in rows] == [1, 2]


def test_ordered_deliveries_rejects_superseded_duplicate_and_foreign() -> None:
    with pytest.raises(SupersededDataError):
        ordered_deliveries([delivery(1, is_current=False)])
    with pytest.raises(DuplicateDeliveryError):
        ordered_deliveries([legal_run(1), legal_run(1)])
    with pytest.raises(DataQualityError):
        ordered_deliveries([delivery(1, innings_id="other")], innings_id=INNINGS)


def test_ordered_events_sorts_and_validates() -> None:
    events = ordered_events(
        [
            event(InningsEventType.RETIREMENT_HURT, player="b", anchor=2),
            event(InningsEventType.RETIREMENT_NOT_OUT, player="a", anchor=None),
        ]
    )
    assert [item.player_id for item in events] == ["a", "b"]
    with pytest.raises(SupersededDataError):
        ordered_events([event(InningsEventType.OTHER, player=None, is_current=False)])
    with pytest.raises(DataQualityError):
        ordered_events(
            [
                event(InningsEventType.RETIREMENT_HURT, player="a", anchor=1),
                event(InningsEventType.RETIREMENT_HURT, player="a", anchor=1),
            ]
        )
    with pytest.raises(DataQualityError):
        ordered_events(
            [event(InningsEventType.RETIREMENT_HURT, player="a", innings_id="other")],
            innings_id=INNINGS,
        )


def test_anchor_validation() -> None:
    rows = [legal_run(1)]
    validate_anchor_sequences([event(InningsEventType.RETIREMENT_HURT, anchor=1)], rows)
    with pytest.raises(InvalidEventError):
        validate_anchor_sequences([event(InningsEventType.RETIREMENT_HURT, anchor=2)], rows)


def test_strictly_increasing_guard() -> None:
    rows = [legal_run(1), legal_run(2)]
    ensure_strictly_increasing(rows)
    with pytest.raises(OutOfOrderDeliveryError):
        ensure_strictly_increasing([legal_run(2), legal_run(1)])
