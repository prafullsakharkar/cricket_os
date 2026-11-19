"""Scenario fixtures for TESTING_STRATEGY §14, at the pure-engine boundary."""

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
    result,
)

from apps.scoring.aggregates import net_run_rate, player_match_line
from apps.scoring.config import preset_for_format
from apps.scoring.engine import rebuild_innings, rebuild_match
from apps.scoring.results import decide_match_result
from apps.scoring.types import (
    DismissalType,
    Format,
    InningsEventType,
    MatchStatus,
    ResultType,
    RetirementType,
    TeamMatchLine,
)

TEST = preset_for_format(Format.TEST)
ODI = preset_for_format(Format.ODI)
T20I = preset_for_format(Format.T20I)
HUNDRED = preset_for_format(Format.THE_HUNDRED)


def test_14_1_test_match_multiple_innings_follow_on_declaration_draw() -> None:
    def context(number: int, team: str, **kwargs: object) -> object:
        return innings(
            [legal_run(seq, 2, innings_id=f"inn{number}") for seq in range(1, 7)],
            innings_id=f"inn{number}",
            innings_number=number,
            batting_team=team,
            bowling_team=TEAM_B if team == TEAM_A else TEAM_A,
            **kwargs,
        )

    contexts = [
        context(1, TEAM_A),
        context(2, TEAM_B),
        context(3, TEAM_B, is_follow_on_innings=True, declared=True),
        context(4, TEAM_A),
    ]
    scorecard = rebuild_match(
        match(
            fmt=Format.TEST,
            status=MatchStatus.COMPLETED,
            overs_limit=None,
            time_expired=True,
        ),
        contexts,  # type: ignore[arg-type]
        TEST,
    )
    assert [row.innings_number for row in scorecard.innings] == [1, 2, 3, 4]
    assert scorecard.innings[2].is_follow_on_innings
    assert scorecard.innings[2].declared
    assert scorecard.result is not None
    assert scorecard.result.result_type is ResultType.DRAW
    line = player_match_line(
        "match-1",
        "bat-1",
        TEAM_A,
        Format.TEST,
        [scorecard.innings[0], scorecard.innings[3]],
        TEST,
    )
    assert line.innings_batted == 2


def test_14_2_odi_revised_target_from_provider() -> None:
    first = result(TEAM_A, 250, legal=300)
    second = result(TEAM_B, 249, wickets=5, legal=240, dls_target=249)
    outcome = decide_match_result(match(is_dls_applied=True), [first, second], ODI)
    assert outcome is not None
    assert outcome.result_type is ResultType.WIN
    assert outcome.margin_type.value == "wickets"
    assert outcome.is_dls


def test_14_3_t20_super_over_is_a_separate_innings() -> None:
    innings_rows = [
        result(TEAM_A, 150, legal=120),
        result(TEAM_B, 150, legal=120),
        result(TEAM_A, 8, legal=5, is_super_over=True, super_over_number=1),
        result(TEAM_B, 9, legal=5, is_super_over=True, super_over_number=1),
    ]
    outcome = decide_match_result(match(fmt=Format.T20I, overs_limit=20), innings_rows, T20I)
    assert outcome is not None
    assert outcome.result_type is ResultType.SUPER_OVER_TIE_BREAK
    assert outcome.winner_team_id == TEAM_B
    assert all(row.is_super_over for row in innings_rows[2:])


def test_14_4_wides_and_no_balls_accounting() -> None:
    rows = [
        delivery(1, wides=1, over_number=1),
        delivery(2, wides=1, byes=2, over_number=1),
        delivery(3, noballs=1, runs=4, over_number=1),
        delivery(4, noballs=1, byes=2, over_number=1),
        *[legal_run(seq, 1, over_number=1) for seq in range(5, 11)],
    ]
    built = rebuild_innings(innings(rows), ODI)
    assert built.totals.legal_balls == 6
    assert built.overs[0].is_complete
    assert built.totals.runs == 18
    assert built.totals.extras.wides == 2
    assert built.totals.extras.noballs == 2
    assert built.totals.extras.byes == 4
    assert built.batting[0].balls == 8
    assert built.bowling[0].runs_conceded == 14


def test_14_5_run_outs_and_dismissals() -> None:
    rows = [
        delivery(
            1,
            is_wicket=True,
            dismissal_type=DismissalType.RUN_OUT,
            dismissed="bat-2",
            fielder1="fielder",
        ),
        delivery(
            2,
            is_wicket=True,
            dismissal_type=DismissalType.STUMPED,
            dismissed="bat-3",
            credited=True,
            fielder1="keeper",
        ),
        delivery(
            3,
            is_wicket=True,
            dismissal_type=DismissalType.CAUGHT,
            dismissed="bat-4",
            credited=True,
            fielder1="fielder",
        ),
        delivery(
            4,
            is_wicket=True,
            dismissal_type=DismissalType.LBW,
            dismissed="bat-5",
            credited=True,
        ),
        delivery(
            5,
            is_wicket=True,
            dismissal_type=DismissalType.RETIRED_OUT,
            dismissed="bat-6",
        ),
    ]
    built = rebuild_innings(innings(rows), ODI)
    assert built.totals.wickets == 5
    assert [fow.wicket_number for fow in built.fall_of_wickets] == [1, 2, 3, 4, 5]
    assert [fow.dismissed_player_id for fow in built.fall_of_wickets] == [
        "bat-2",
        "bat-3",
        "bat-4",
        "bat-5",
        "bat-6",
    ]
    assert built.bowling[0].wickets == 3  # run-out and retired-out are not credited
    run_out = next(line for line in built.batting if line.player_id == "bat-2")
    assert run_out.fielder1_id == "fielder"


def test_14_5_retired_hurt_is_not_out_and_adds_no_fow() -> None:
    rows = [
        legal_run(1),
        delivery(2, retired="bat-2", retirement_type=RetirementType.HURT),
        legal_run(3, 1, batter="bat-3"),
    ]
    events = [event(InningsEventType.RESUME, player="bat-2", anchor=3)]
    built = rebuild_innings(innings(rows, events), ODI)
    assert built.totals.wickets == 0
    assert built.fall_of_wickets == ()
    assert built.retirements[0].resumed is True
    hurt = next(line for line in built.batting if line.player_id == "bat-2")
    assert hurt.out is False
    assert hurt.is_not_out


def test_14_6_abandoned_match_contributes_nothing_to_nrr() -> None:
    outcome = decide_match_result(match(status=MatchStatus.ABANDONED), [], ODI)
    assert outcome is not None and outcome.result_type is ResultType.NO_RESULT
    line = TeamMatchLine(
        rule_version="v",
        match_id="m",
        team_id=TEAM_A,
        format=Format.ODI,
        runs_for=200,
        balls_faced=100,
        runs_against=100,
        balls_bowled=100,
        is_no_result=True,
    )
    assert net_run_rate(TEAM_A, [line], ODI) is None


def test_14_7_partial_historical_data_is_marked_estimated() -> None:
    built = rebuild_innings(innings(), ODI, estimated=True)
    assert built.estimated and built.totals.estimated
    assert built.batting == ()


def test_14_8_provider_correction_rebuilds_from_current_rows() -> None:
    original = rebuild_innings(innings([legal_run(1, 2)]), ODI)
    corrected = rebuild_innings(innings([legal_run(1, 6)]), ODI)
    assert original.totals.runs == 2
    assert corrected.totals.runs == 6
    assert corrected.totals.runs != original.totals.runs


def test_14_9_off_ball_retirement_and_resume() -> None:
    rows = [
        legal_run(1, 4),
        legal_run(2, 1),
        legal_run(3, 2, batter="bat-3"),
        legal_run(4, 3),
    ]
    events = [
        event(InningsEventType.RETIREMENT_HURT, player="bat-1", anchor=2),
        event(InningsEventType.RESUME, player="bat-1", anchor=3),
    ]
    built = rebuild_innings(innings(rows, events), ODI)
    assert built.totals.wickets == 0
    assert built.fall_of_wickets == ()
    assert len(built.partnerships) == 3
    assert built.partnerships[2].batter1_id == "bat-1"
    assert len(built.retirements) == 1
    assert built.retirements[0].source.value == "event"
    assert built.retirements[0].anchor_delivery_seq == 2
    assert built.retirements[0].resumed is True


def test_14_10_hundred_uses_five_ball_sets() -> None:
    rows = [
        delivery(
            seq,
            runs=1,
            over_number=(seq - 1) // 5 + 1,
            ball_in_over=(seq - 1) % 5 + 1,
        )
        for seq in range(1, 101)
    ]
    built = rebuild_innings(innings(rows), HUNDRED)
    assert built.totals.legal_balls == 100
    assert built.totals.overs_display == "20.0"
    assert len(built.overs) == 20
    assert all(over.is_complete and over.legal_balls == 5 for over in built.overs)
    assert built.bowling[0].economy == pytest.approx(5.0)
    assert HUNDRED.total_balls == 100
