"""Result precedence, margins, DLS targets, draws and Super Overs."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import TEAM_A, TEAM_B, match, result

from apps.scoring.config import RuleConfig, preset_for_format
from apps.scoring.errors import MissingDlsTargetError
from apps.scoring.results import decide_match_result, decided_result, quota_balls
from apps.scoring.types import (
    Format,
    MarginType,
    MatchStatus,
    ResultType,
)

ODI = preset_for_format(Format.ODI)
T20I = preset_for_format(Format.T20I)
TEST = preset_for_format(Format.TEST)


def test_chasing_side_wins_by_wickets() -> None:
    first = result(TEAM_A, 300, legal=300)
    second = result(TEAM_B, 301, wickets=4, legal=250)
    outcome = decide_match_result(match(), [first, second], ODI)
    assert outcome is not None
    assert outcome.result_type is ResultType.WIN
    assert outcome.winner_team_id == TEAM_B
    assert outcome.margin_type is MarginType.WICKETS
    assert outcome.margin_value == 6
    assert outcome.rule_version == ODI.version
    assert outcome.result_text == "won by 6 wickets"


def test_defending_side_wins_by_runs() -> None:
    first = result(TEAM_A, 300, legal=300)
    second = result(TEAM_B, 250, all_out=True, legal=240)
    outcome = decide_match_result(match(), [first, second], ODI)
    assert outcome is not None
    assert outcome.winner_team_id == TEAM_A
    assert outcome.margin_type is MarginType.RUNS
    assert outcome.margin_value == 50
    assert outcome.result_text == "won by 50 runs"


def test_tie_when_scores_equal_and_overs_complete() -> None:
    first = result(TEAM_A, 250, all_out=True, legal=300)
    second = result(TEAM_B, 250, wickets=8, legal=300)
    outcome = decide_match_result(match(), [first, second], ODI)
    assert outcome is not None
    assert outcome.result_type is ResultType.TIE
    assert outcome.margin_type is MarginType.TIE


def test_quota_completion_uses_reduced_overs() -> None:
    first = result(TEAM_A, 120, legal=120)
    second = result(TEAM_B, 119, legal=120)
    outcome = decide_match_result(match(overs_limit=20), [first, second], ODI)
    assert outcome is not None
    assert outcome.result_type is ResultType.WIN
    assert outcome.margin_value == 1
    assert outcome.result_text == "won by 1 run"


@pytest.mark.parametrize(
    "status", [MatchStatus.ABANDONED, MatchStatus.CANCELLED, MatchStatus.POSTPONED]
)
def test_abandoned_cancelled_postponed_are_no_result(status: MatchStatus) -> None:
    outcome = decide_match_result(match(status=status), [], ODI)
    assert outcome is not None
    assert outcome.result_type is ResultType.NO_RESULT


def test_forfeit() -> None:
    outcome = decide_match_result(match(status=MatchStatus.FORFEITED), [], ODI)
    assert outcome is not None
    assert outcome.result_type is ResultType.FORFEIT


def test_unfinished_match_has_no_result_yet() -> None:
    first = result(TEAM_A, 100, legal=60)
    second = result(TEAM_B, 40, legal=30)
    assert decide_match_result(match(), [first, second], ODI) is None
    assert decided_result(match(), [first, second], ODI) is None


def test_completed_one_innings_is_no_result() -> None:
    outcome = decide_match_result(
        match(status=MatchStatus.COMPLETED), [result(TEAM_A, 100, all_out=True)], ODI
    )
    assert outcome is not None and outcome.result_type is ResultType.NO_RESULT


def test_single_innings_still_in_progress_has_no_result() -> None:
    assert (
        decide_match_result(
            match(status=MatchStatus.IN_PROGRESS),
            [result(TEAM_A, 100, legal=60)],
            ODI,
        )
        is None
    )


def test_completed_chase_that_stopped_is_no_result() -> None:
    first = result(TEAM_A, 300, legal=300)
    second = result(TEAM_B, 100, legal=60)
    outcome = decide_match_result(match(status=MatchStatus.COMPLETED), [first, second], ODI)
    assert outcome is not None and outcome.result_type is ResultType.NO_RESULT


def test_test_draw_when_time_expires() -> None:
    innings = [
        result(TEAM_A, 400, legal=600, innings_number=1),
        result(TEAM_B, 300, legal=500, innings_number=2),
        result(TEAM_A, 200, legal=300, innings_number=3, declared=True),
        result(TEAM_B, 150, legal=300, innings_number=4),
    ]
    outcome = decide_match_result(
        match(
            fmt=Format.TEST,
            status=MatchStatus.COMPLETED,
            overs_limit=None,
            time_expired=True,
        ),
        innings,
        TEST,
    )
    assert outcome is not None and outcome.result_type is ResultType.DRAW


def test_test_chase_wins_by_wickets() -> None:
    innings = [
        result(TEAM_A, 400, legal=600, innings_number=1),
        result(TEAM_B, 300, legal=500, innings_number=2),
        result(TEAM_A, 200, legal=300, innings_number=3, declared=True),
        result(TEAM_B, 301, wickets=3, legal=500, innings_number=4),
    ]
    outcome = decide_match_result(match(fmt=Format.TEST, overs_limit=None), innings, TEST)
    assert outcome is not None
    assert outcome.winner_team_id == TEAM_B
    assert outcome.margin_type is MarginType.WICKETS
    assert outcome.margin_value == 7


def test_test_win_by_an_innings_and_single_run_margin() -> None:
    innings_rows = [
        result(TEAM_A, 300, legal=600, innings_number=1),
        result(TEAM_B, 200, all_out=True, legal=400, innings_number=2),
        result(TEAM_B, 99, all_out=True, legal=400, innings_number=3),
    ]
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.COMPLETED, overs_limit=None),
        innings_rows,
        TEST,
    )
    assert outcome is not None
    assert outcome.margin_type is MarginType.INNINGS
    assert outcome.margin_value == 1
    assert outcome.result_text == "won by an innings and 1 run"


def test_test_aggregate_tie() -> None:
    innings_rows = [
        result(TEAM_A, 300, legal=600, innings_number=1),
        result(TEAM_B, 300, legal=600, innings_number=2),
        result(TEAM_A, 150, legal=300, innings_number=3, declared=True),
        result(TEAM_B, 150, all_out=True, legal=300, innings_number=4),
    ]
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.COMPLETED, overs_limit=None),
        innings_rows,
        TEST,
    )
    assert outcome is not None and outcome.result_type is ResultType.TIE


def test_test_time_expired_draw_even_while_marked_in_progress() -> None:
    innings_rows = [
        result(TEAM_A, 400, legal=600, innings_number=1),
        result(TEAM_B, 300, legal=500, innings_number=2),
        result(TEAM_A, 200, legal=300, innings_number=3, declared=True),
        result(TEAM_B, 150, legal=300, innings_number=4),
    ]
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.IN_PROGRESS, overs_limit=None, time_expired=True),
        innings_rows,
        TEST,
    )
    assert outcome is not None and outcome.result_type is ResultType.DRAW


def test_only_super_over_innings_without_regulation_is_no_result() -> None:
    outcome = decide_match_result(
        match(status=MatchStatus.COMPLETED),
        [result(TEAM_A, 10, legal=5, is_super_over=True, super_over_number=1)],
        ODI,
    )
    assert outcome is not None and outcome.result_type is ResultType.NO_RESULT


def test_test_win_by_an_innings() -> None:
    innings = [
        result(TEAM_A, 500, legal=600, innings_number=1),
        result(TEAM_B, 200, all_out=True, legal=400, innings_number=2),
        result(TEAM_B, 250, all_out=True, legal=400, innings_number=3),
    ]
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.COMPLETED, overs_limit=None),
        innings,
        TEST,
    )
    assert outcome is not None
    assert outcome.winner_team_id == TEAM_A
    assert outcome.margin_type is MarginType.INNINGS
    assert outcome.margin_value == 50


def test_test_win_by_runs_when_both_sides_batted_twice() -> None:
    innings = [
        result(TEAM_A, 500, legal=600, innings_number=1),
        result(TEAM_B, 200, all_out=True, legal=400, innings_number=2),
        result(TEAM_A, 300, declared=True, legal=400, innings_number=3),
        result(TEAM_B, 250, all_out=True, legal=400, innings_number=4),
    ]
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.COMPLETED, overs_limit=None),
        innings,
        TEST,
    )
    assert outcome is not None
    assert outcome.winner_team_id == TEAM_A
    assert outcome.margin_type is MarginType.RUNS
    assert outcome.margin_value == 350


def test_test_completed_one_innings_is_no_result() -> None:
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.COMPLETED, overs_limit=None),
        [result(TEAM_A, 100, legal=100)],
        TEST,
    )
    assert outcome is not None and outcome.result_type is ResultType.NO_RESULT


def test_test_completed_without_all_out_still_decides_on_aggregate() -> None:
    innings = [
        result(TEAM_A, 300, legal=500, innings_number=1),
        result(TEAM_B, 200, all_out=True, legal=400, innings_number=2),
        result(TEAM_A, 100, legal=200, innings_number=3),
        result(TEAM_B, 100, legal=200, innings_number=4),
    ]
    outcome = decide_match_result(
        match(fmt=Format.TEST, status=MatchStatus.COMPLETED, overs_limit=None),
        innings,
        TEST,
    )
    assert outcome is not None
    assert outcome.result_type is ResultType.WIN
    assert outcome.winner_team_id == TEAM_A


def test_dls_uses_the_provider_target() -> None:
    first = result(TEAM_A, 250, legal=300)
    second = result(TEAM_B, 249, wickets=5, legal=240, dls_target=249)
    outcome = decide_match_result(match(is_dls_applied=True), [first, second], ODI)
    assert outcome is not None
    assert outcome.winner_team_id == TEAM_B
    assert outcome.margin_type is MarginType.WICKETS
    assert outcome.margin_value == 5
    assert outcome.is_dls


def test_dls_missing_target_is_rejected() -> None:
    first = result(TEAM_A, 250, legal=300)
    second = result(TEAM_B, 200, legal=240)
    with pytest.raises(MissingDlsTargetError):
        decide_match_result(match(is_dls_applied=True), [first, second], ODI)


def test_dls_short_chase_is_still_in_progress() -> None:
    first = result(TEAM_A, 250, legal=300)
    second = result(TEAM_B, 249, legal=240, dls_target=250)
    assert decide_match_result(match(is_dls_applied=True), [first, second], ODI) is None


def _tied_regulation() -> list[object]:
    return [
        result(TEAM_A, 150, legal=120, innings_number=1),
        result(TEAM_B, 150, legal=120, innings_number=2),
    ]


def test_super_over_tie_break() -> None:
    innings = [
        *_tied_regulation(),
        result(TEAM_A, 10, legal=5, is_super_over=True, super_over_number=1),
        result(TEAM_B, 12, legal=5, is_super_over=True, super_over_number=1),
    ]
    outcome = decide_match_result(match(fmt=Format.T20I, overs_limit=20), innings, T20I)
    assert outcome is not None
    assert outcome.result_type is ResultType.SUPER_OVER_TIE_BREAK
    assert outcome.winner_team_id == TEAM_B
    assert outcome.is_super_over
    assert outcome.margin_value == 2


def test_a_tied_super_over_reports_a_tie_break_without_winner() -> None:
    innings = [
        *_tied_regulation(),
        result(TEAM_A, 10, legal=5, is_super_over=True, super_over_number=1),
        result(TEAM_B, 10, legal=5, is_super_over=True, super_over_number=1),
    ]
    outcome = decide_match_result(match(fmt=Format.T20I, overs_limit=20), innings, T20I)
    assert outcome is not None
    assert outcome.result_type is ResultType.SUPER_OVER_TIE_BREAK
    assert outcome.winner_team_id is None
    assert outcome.margin_type is MarginType.TIE


def test_second_super_over_decides() -> None:
    innings = [
        *_tied_regulation(),
        result(TEAM_A, 10, legal=5, is_super_over=True, super_over_number=1),
        result(TEAM_B, 10, legal=5, is_super_over=True, super_over_number=1),
        result(TEAM_A, 5, legal=5, is_super_over=True, super_over_number=2),
        result(TEAM_B, 6, legal=5, is_super_over=True, super_over_number=2),
    ]
    outcome = decide_match_result(match(fmt=Format.T20I, overs_limit=20), innings, T20I)
    assert outcome is not None
    assert outcome.winner_team_id == TEAM_B


def test_incomplete_super_over_falls_back_to_the_tie() -> None:
    innings = [
        *_tied_regulation(),
        result(TEAM_A, 10, legal=5, is_super_over=True, super_over_number=1),
    ]
    outcome = decide_match_result(match(fmt=Format.T20I, overs_limit=20), innings, T20I)
    assert outcome is not None and outcome.result_type is ResultType.TIE


def test_super_over_can_be_disabled() -> None:
    config = RuleConfig(version="v", overs_limit=20, max_innings=2, super_over_enabled=False)
    innings = [
        *_tied_regulation(),
        result(TEAM_A, 10, legal=5, is_super_over=True, super_over_number=1),
        result(TEAM_B, 12, legal=5, is_super_over=True, super_over_number=1),
    ]
    outcome = decide_match_result(match(fmt=Format.T20I, overs_limit=20), innings, config)
    assert outcome is not None and outcome.result_type is ResultType.TIE


def test_quota_balls_helper() -> None:
    row = result(TEAM_A, 10, legal=1)
    assert quota_balls(row, match(overs_limit=20), ODI) == 120
    assert quota_balls(row, match(overs_limit=None), TEST) is None
