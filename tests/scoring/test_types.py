"""Frozen input/output types, validation and metric classification."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from tests.scoring.helpers import (
    INNINGS,
    TEAM_A,
    delivery,
    event,
    innings,
    match,
)

from apps.scoring.classification import MetricClass
from apps.scoring.errors import (
    InvalidDeliveryError,
    InvalidEventError,
    InvalidInningsError,
)
from apps.scoring.types import (
    BattingLine,
    DismissalType,
    InningsEvent,
    InningsEventType,
    MatchContext,
    MatchStatus,
    Totals,
)


def test_delivery_derived_columns() -> None:
    row = delivery(1, runs=4, wides=1, noballs=1, byes=2, leg_byes=1, penalty=3)
    assert row.total_runs == 12
    assert row.extras_component == 8
    assert row.is_wide and row.is_noball
    assert not row.is_legal
    legal = delivery(2)
    assert legal.is_legal and not legal.is_wide and not legal.is_noball


def test_delivery_is_immutable() -> None:
    row = delivery(1)
    with pytest.raises(FrozenInstanceError):
        row.runs_off_bat = 5  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"delivery_seq": 0},
        {"over_number": 0},
        {"ball_in_over": 0},
        {"runs_off_bat": -1},
        {"wides": -1},
        {"noballs": -1},
        {"byes": -1},
        {"leg_byes": -1},
        {"penalty": -1},
        {"is_wicket": True},
        {"dismissal_type": DismissalType.BOWLED},
        {"dismissed": "bat-1"},
        {"credited": True},
        {"retired": "bat-1"},
        {"retirement_type": "HURT"},
    ],
)
def test_delivery_validation(kwargs: dict[str, object]) -> None:
    base: dict[str, object] = {"delivery_seq": 1}
    base.update(kwargs)
    with pytest.raises(InvalidDeliveryError) as excinfo:
        delivery(**base)  # type: ignore[arg-type]
    assert excinfo.value.delivery_seq is not None


def test_delivery_wicket_and_retirement_valid_paths() -> None:
    wicket = delivery(
        1,
        is_wicket=True,
        dismissal_type=DismissalType.BOWLED,
        dismissed="bat-1",
        credited=True,
    )
    assert wicket.is_wicket
    retired = delivery(2, retired="bat-1", retirement_type="HURT")
    assert retired.retirement_type is not None


def test_event_validation() -> None:
    with pytest.raises(InvalidEventError):
        InningsEvent(INNINGS, InningsEventType.RETIREMENT_HURT)
    with pytest.raises(InvalidEventError):
        InningsEvent(INNINGS, InningsEventType.RESUME, "p", anchor_delivery_seq=0)
    with pytest.raises(InvalidEventError):
        InningsEvent(INNINGS, InningsEventType.OTHER, over_number=0)
    with pytest.raises(InvalidEventError):
        InningsEvent(INNINGS, InningsEventType.OTHER, revision=0)
    other = InningsEvent(INNINGS, InningsEventType.OTHER)
    assert other.retirement_type is None and not other.is_retirement
    hurt = event(InningsEventType.RETIREMENT_HURT)
    assert hurt.is_retirement and hurt.retirement_type.value == "HURT"
    not_out = event(InningsEventType.RETIREMENT_NOT_OUT)
    assert not_out.retirement_type.value == "NOT_OUT"


def test_innings_context_validation() -> None:
    with pytest.raises(InvalidInningsError):
        innings(innings_number=0)
    with pytest.raises(InvalidInningsError):
        innings(innings_number=5)
    with pytest.raises(InvalidInningsError):
        innings(batting_team=TEAM_A, bowling_team=TEAM_A)
    with pytest.raises(InvalidInningsError):
        innings(is_super_over=True)
    with pytest.raises(InvalidInningsError):
        innings(declared=True, all_out=True)
    with pytest.raises(InvalidInningsError):
        innings(target=-1)
    with pytest.raises(InvalidInningsError):
        innings(target_balls=-1)
    with pytest.raises(InvalidInningsError):
        innings(dls_target=-1)
    with pytest.raises(InvalidInningsError):
        innings([delivery(1, innings_id="other")])
    with pytest.raises(InvalidInningsError):
        innings(events=[event(InningsEventType.RESUME, innings_id="other")])


def test_match_context_validation() -> None:
    with pytest.raises(InvalidInningsError):
        MatchContext("m", "ODI", MatchStatus.IN_PROGRESS, (TEAM_A,))  # type: ignore[arg-type]
    with pytest.raises(InvalidInningsError):
        MatchContext("m", "ODI", MatchStatus.IN_PROGRESS, (TEAM_A, TEAM_A))  # type: ignore[arg-type]
    with pytest.raises(InvalidInningsError):
        match(overs_limit=0)
    with pytest.raises(InvalidInningsError):
        match(balls_per_over=0)


def test_output_classification() -> None:
    assert BattingLine.classification_of("runs") is MetricClass.OFFICIAL
    assert BattingLine.classification_of("dot_pct") is MetricClass.DERIVED
    assert BattingLine.classification_of("boundary_pct") is MetricClass.DERIVED
    assert BattingLine.classification_of("estimated") is MetricClass.DERIVED
    assert BattingLine.derived_fields() == {"dot_pct", "boundary_pct", "estimated"}
    assert Totals.classification_of("run_rate") is MetricClass.OFFICIAL
    with pytest.raises(KeyError):
        Totals.classification_of("nope")


def test_totals_default_extras_and_rule_version() -> None:
    totals = Totals(rule_version="v1", runs=10)
    assert totals.extras.total == 0
    assert totals.rule_version == "v1"
