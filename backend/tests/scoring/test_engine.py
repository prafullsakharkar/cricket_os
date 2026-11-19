"""Engine integration at the pure-function boundary."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import (
    TEAM_A,
    TEAM_B,
    delivery,
    event,
    innings,
    legal_run,
    match,
)

from apps.scoring.config import preset_for_format
from apps.scoring.engine import rebuild_innings, rebuild_match
from apps.scoring.errors import (
    DataQualityError,
    DuplicateDeliveryError,
    InvalidEventError,
    InvalidInningsError,
    RetirementSourceConflictError,
    SupersededDataError,
)
from apps.scoring.partnerships import (
    build_partnerships,
    innings_retirements,
    merged_timeline,
    validate_single_retirement_source,
)
from apps.scoring.types import (
    DismissalType,
    Format,
    InningsEventType,
    InningsStatus,
    MatchStatus,
    RetirementSource,
    RetirementType,
)

ODI = preset_for_format(Format.ODI)


def mixed_innings() -> object:
    rows = [
        legal_run(1, 4),
        delivery(2, wides=1),
        delivery(3, noballs=1, runs=4),
        delivery(4, byes=2),
        delivery(5, leg_byes=1),
        delivery(6, penalty=5),
        legal_run(7, 1),
        delivery(
            8,
            is_wicket=True,
            dismissal_type=DismissalType.BOWLED,
            dismissed="bat-1",
            credited=True,
        ),
    ]
    return innings(rows)


def test_rebuild_totals_batting_bowling_and_overs() -> None:
    result = rebuild_innings(mixed_innings(), ODI)  # type: ignore[arg-type]
    assert result.totals.runs == 19
    assert result.totals.wickets == 1
    assert result.totals.legal_balls == 6
    assert result.totals.overs_display == "1.0"
    assert result.totals.run_rate == pytest.approx(19.0)
    assert result.totals.extras.wides == 1
    assert result.totals.extras.total == 10
    assert result.all_out is False
    assert result.status is InningsStatus.IN_PROGRESS
    assert result.rule_version == ODI.version

    batting = {line.player_id: line for line in result.batting}
    assert batting["bat-1"].runs == 9
    assert batting["bat-1"].balls == 7
    assert batting["bat-1"].fours == 2
    assert batting["bat-1"].dots == 1
    assert batting["bat-1"].out
    assert batting["bat-2"].did_bat
    assert batting["bat-2"].is_not_out

    bowling = result.bowling[0]
    assert bowling.runs_conceded == 11
    assert bowling.legal_balls == 6
    assert bowling.wickets == 1
    assert bowling.economy == pytest.approx(11.0)

    assert len(result.overs) == 2
    assert result.overs[0].runs_conceded == 10
    assert result.overs[0].legal_balls == 4
    assert not result.overs[0].is_complete
    assert result.overs[1].runs_conceded == 1


def test_fow_and_partnership_for_a_normal_wicket() -> None:
    result = rebuild_innings(mixed_innings(), ODI)  # type: ignore[arg-type]
    assert len(result.fall_of_wickets) == 1
    fow = result.fall_of_wickets[0]
    assert (fow.wicket_number, fow.score, fow.over_display, fow.delivery_seq) == (1, 19, "1.0", 8)
    assert fow.dismissed_player_id == "bat-1"
    assert len(result.partnerships) == 1
    partnership = result.partnerships[0]
    assert partnership.runs == 19
    assert partnership.balls == 6
    assert partnership.unbroken is False
    assert partnership.wicket_number == 0


def test_empty_innings_is_zero_not_an_error() -> None:
    result = rebuild_innings(innings(), ODI)
    assert result.totals.runs == 0
    assert result.totals.run_rate is None
    assert result.batting == () and result.bowling == ()
    assert result.partnerships == ()


def test_on_ball_retirement_breaks_partnership_without_fow() -> None:
    rows = [
        legal_run(1, 4),
        delivery(2, retired="bat-1", retirement_type=RetirementType.HURT),
        legal_run(3, 2, batter="bat-3"),
    ]
    result = rebuild_innings(innings(rows), ODI)
    assert result.totals.wickets == 0
    assert result.fall_of_wickets == ()
    assert len(result.partnerships) == 2
    assert result.partnerships[0].runs == 4
    assert result.partnerships[0].unbroken is False
    assert result.partnerships[1].batter1_id == "bat-3"
    assert result.retirements[0].source is RetirementSource.DELIVERY
    assert result.retirements[0].anchor_delivery_seq == 2
    assert result.batting[0].out is False


def test_retired_out_is_a_wicket_and_fow() -> None:
    rows = [
        legal_run(1, 1),
        delivery(
            2,
            is_wicket=True,
            dismissal_type=DismissalType.RETIRED_OUT,
            dismissed="bat-1",
            retired=None,
        ),
    ]
    result = rebuild_innings(innings(rows), ODI)
    assert result.totals.wickets == 1
    assert len(result.fall_of_wickets) == 1
    assert result.fall_of_wickets[0].dismissed_player_id == "bat-1"


def test_off_ball_retirement_is_read_once_and_not_a_wicket() -> None:
    rows = [legal_run(1, 4), legal_run(2, 1, batter="bat-3")]
    events = [event(InningsEventType.RETIREMENT_HURT, player="bat-1", anchor=1)]
    result = rebuild_innings(innings(rows, events), ODI)
    assert result.totals.wickets == 0
    assert result.fall_of_wickets == ()
    assert len(result.retirements) == 1
    record = result.retirements[0]
    assert record.source is RetirementSource.EVENT
    assert record.anchor_delivery_seq == 1
    assert record.kind is RetirementType.HURT
    assert len(result.partnerships) == 2


def test_off_ball_retirement_before_first_ball() -> None:
    events = [event(InningsEventType.RETIREMENT_NOT_OUT, player="bat-1", anchor=None)]
    rows = [legal_run(1, 2, batter="bat-3")]
    result = rebuild_innings(innings(rows, events), ODI)
    assert result.retirements[0].anchor_delivery_seq is None
    assert result.partnerships[0].batter1_id == "bat-3"


def test_retirement_never_recorded_twice() -> None:
    rows = [delivery(1, retired="bat-1", retirement_type=RetirementType.HURT)]
    events = [event(InningsEventType.RETIREMENT_HURT, player="bat-1", anchor=1)]
    with pytest.raises(RetirementSourceConflictError):
        validate_single_retirement_source(rows, events)
    with pytest.raises(RetirementSourceConflictError):
        rebuild_innings(innings(rows, events), ODI)


def test_resume_event_reopens_a_partnership_and_marks_resumed() -> None:
    rows = [
        legal_run(1, 4),
        legal_run(2, 1, batter="bat-3"),
        legal_run(3, 2),
    ]
    events = [
        event(InningsEventType.RETIREMENT_HURT, player="bat-1", anchor=1),
        event(InningsEventType.RESUME, player="bat-1", anchor=2),
    ]
    result = rebuild_innings(innings(rows, events), ODI)
    assert result.totals.wickets == 0
    assert len(result.partnerships) == 3
    assert result.partnerships[2].batter1_id == "bat-1"
    assert result.retirements[0].resumed is True
    assert result.batting[0].out is False


def test_pair_change_without_break_is_rejected() -> None:
    rows = [legal_run(1), legal_run(2, batter="bat-3")]
    with pytest.raises(DataQualityError):
        rebuild_innings(innings(rows), ODI)


def test_duplicate_and_superseded_rows_are_rejected() -> None:
    with pytest.raises(DuplicateDeliveryError):
        rebuild_innings(innings([legal_run(1), legal_run(1)]), ODI)
    with pytest.raises(SupersededDataError):
        rebuild_innings(innings([delivery(1, is_current=False)]), ODI)
    with pytest.raises(SupersededDataError):
        rebuild_innings(
            innings(events=[event(InningsEventType.OTHER, player=None, is_current=False)]),
            ODI,
        )


def test_event_anchor_must_exist() -> None:
    events = [event(InningsEventType.RETIREMENT_HURT, player="bat-1", anchor=9)]
    with pytest.raises(InvalidEventError):
        rebuild_innings(innings([legal_run(1)], events), ODI)


def test_merged_timeline_order() -> None:
    rows = [legal_run(1), legal_run(2)]
    events = [
        event(InningsEventType.RETIREMENT_HURT, player="x", anchor=None),
        event(InningsEventType.RETIREMENT_NOT_OUT, player="y", anchor=1),
    ]
    nodes = merged_timeline(rows, events)
    assert isinstance(nodes[0], type(events[0]))
    assert nodes[0].anchor_delivery_seq is None
    assert nodes[1].delivery_seq == 1
    assert nodes[2].anchor_delivery_seq == 1
    assert nodes[3].delivery_seq == 2


def test_declared_and_all_out_statuses() -> None:
    declared = rebuild_innings(innings([legal_run(1)], declared=True), ODI)
    assert declared.status is InningsStatus.DECLARED
    all_out = rebuild_innings(innings([legal_run(1)], all_out=True), ODI)
    assert all_out.status is InningsStatus.ALL_OUT
    assert all_out.all_out is True


def test_estimated_flag_propagates() -> None:
    result = rebuild_innings(innings([legal_run(1)]), ODI, estimated=True)
    assert result.estimated and result.totals.estimated
    assert result.batting[0].estimated and result.bowling[0].estimated


def test_super_over_flags_are_preserved() -> None:
    context = innings([legal_run(1)], is_super_over=True, super_over_number=1)
    result = rebuild_innings(context, ODI)
    assert result.is_super_over and result.super_over_number == 1


def test_rebuild_is_deterministic_and_order_independent() -> None:
    rows = [legal_run(1, 1), legal_run(2, 2), legal_run(3, 3)]
    forward = rebuild_innings(innings(rows), ODI)
    backward = rebuild_innings(innings(list(reversed(rows))), ODI)
    assert forward == backward


def test_rebuild_match_rejects_too_many_innings() -> None:
    contexts = [innings(innings_id=f"i{i}", innings_number=i + 1) for i in range(3)]
    with pytest.raises(InvalidInningsError):
        rebuild_match(match(), contexts, ODI)


def test_rebuild_match_assembles_innings_and_result() -> None:
    first = innings(
        [legal_run(1, 4, innings_id="i1")],
        innings_id="i1",
        innings_number=1,
        batting_team=TEAM_A,
        bowling_team=TEAM_B,
    )
    second = innings(
        [legal_run(1, 1, innings_id="i2")],
        innings_id="i2",
        innings_number=2,
        batting_team=TEAM_B,
        bowling_team=TEAM_A,
    )
    scorecard = rebuild_match(match(status=MatchStatus.IN_PROGRESS), [first, second], ODI)
    assert len(scorecard.innings) == 2
    assert scorecard.result is None
    assert scorecard.match_id == "match-1"


def test_retirement_kind_mapping() -> None:
    from apps.scoring.errors import DataQualityError
    from apps.scoring.partnerships import retirement_kind

    assert retirement_kind("HURT") is RetirementType.HURT
    assert retirement_kind("NOT_OUT") is RetirementType.NOT_OUT
    with pytest.raises(DataQualityError):
        retirement_kind("SOMETIMES")


def test_build_partnerships_public_api() -> None:
    context = innings([legal_run(1), legal_run(2, batter="bat-3")])
    with pytest.raises(DataQualityError):
        build_partnerships(context, ODI)


def test_innings_retirements_sorted_with_none_first() -> None:
    rows = [delivery(1, retired="b", retirement_type=RetirementType.HURT)]
    events = [event(InningsEventType.RETIREMENT_NOT_OUT, player="a", anchor=None)]
    records = innings_retirements(rows, events, "v")
    assert [record.player_id for record in records] == ["a", "b"]
