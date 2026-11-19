"""Rule-level unit tests for E1-E25 and the §5 named examples."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import delivery, legal_run

from apps.scoring.config import RuleConfig, preset_for_format
from apps.scoring.errors import InvalidDeliveryError, InvalidStatInputError
from apps.scoring.laws import (
    balls_remaining,
    batter_faces_ball,
    batting_average,
    batting_line,
    boundary_pct,
    bowler_runs_charged,
    bowling_average,
    bowling_line,
    bowling_strike_rate,
    current_run_rate,
    dismissal_for,
    dot_pct,
    economy,
    extras_breakdown,
    is_batter_dot,
    is_bowler_wicket,
    is_bowling_dot,
    legal_balls,
    maidens_of,
    nrr_overs,
    over_is_complete,
    overs_display,
    overs_from_balls,
    overs_of,
    required_run_rate,
    safe_div,
    strike_rate,
    total_balls,
    wicket_count,
)
from apps.scoring.laws.dismissals import (
    batter_appearances,
    bowler_appearances,
    is_team_wicket,
    retirement_type_for,
)
from apps.scoring.types import DismissalType, RetirementType

ODI = preset_for_format("ODI")  # type: ignore[arg-type]
HUNDRED = preset_for_format("THE_HUNDRED")  # type: ignore[arg-type]


def test_e1_batting_average_undefined_when_never_out() -> None:
    assert batting_average(runs=500, innings=10, not_outs=10) is None


def test_batting_average_excludes_not_outs() -> None:
    assert batting_average(1000, 20, 5) == pytest.approx(66.666, 0.01)


def test_batting_average_rejects_impossible_inputs() -> None:
    with pytest.raises(InvalidStatInputError):
        batting_average(-1, 1, 0)
    with pytest.raises(InvalidStatInputError):
        batting_average(1, -1, 0)
    with pytest.raises(InvalidStatInputError):
        batting_average(1, 1, -1)
    with pytest.raises(InvalidStatInputError):
        batting_average(1, 1, 2)


def test_e2_bowling_average_and_sr_undefined_without_wickets() -> None:
    assert bowling_average(50, 0) is None
    assert bowling_strike_rate(60, 0) is None


def test_e3_zero_balls_undefined() -> None:
    assert strike_rate(0, 0) is None
    assert strike_rate(10, 0) is None
    assert economy(10, 0, ODI) is None
    assert current_run_rate(0, 0, ODI) is None
    with pytest.raises(InvalidStatInputError):
        strike_rate(-1, 1)
    with pytest.raises(InvalidStatInputError):
        strike_rate(1, -1)
    with pytest.raises(InvalidStatInputError):
        economy(1, -1, ODI)


def test_safe_div_only_returns_none_on_zero() -> None:
    assert safe_div(1, 2) == 0.5
    assert safe_div(0, 2) == 0.0
    assert safe_div(1, 0) is None


def test_e4_wide_is_not_legal_and_never_faces_a_ball() -> None:
    row = delivery(1, wides=1)
    assert not row.is_legal
    assert row.total_runs == 1
    assert not batter_faces_ball(row, ODI)
    assert bowler_runs_charged(row) == 1


def test_e5_noball_off_the_bat() -> None:
    row = delivery(1, noballs=1, runs=4)
    assert not row.is_legal
    assert row.total_runs == 5
    assert batter_faces_ball(row, ODI)
    assert bowler_runs_charged(row) == 5
    no_face = RuleConfig(version="x", counts_ball_on_noball=False)
    assert not batter_faces_ball(row, no_face)


def test_e6_noball_plus_byes() -> None:
    row = delivery(1, noballs=1, byes=2)
    assert row.total_runs == 3
    assert bowler_runs_charged(row) == 1
    assert row.byes == 2


def test_e7_byes_and_leg_byes_are_legal_and_not_charged() -> None:
    for row in (delivery(1, byes=2), delivery(2, leg_byes=3)):
        assert row.is_legal
        assert bowler_runs_charged(row) == 0
        assert row.total_runs in {2, 3}


def test_e8_penalty_counts_in_total_only() -> None:
    row = delivery(1, penalty=5)
    assert row.total_runs == 5
    assert bowler_runs_charged(row) == 0
    assert row.extras_component == 5


def test_e9_run_out_is_a_team_wicket_but_not_a_bowler_wicket() -> None:
    row = delivery(
        1,
        is_wicket=True,
        dismissal_type=DismissalType.RUN_OUT,
        dismissed="bat-2",
        credited=False,
    )
    assert is_team_wicket(row)
    assert not is_bowler_wicket(row)
    assert wicket_count([row], ODI) == 1
    assert not bowling_line("bowl-1", [row], ODI, "v").wickets


def test_e10_retired_hurt_is_not_a_wicket() -> None:
    rows = [
        legal_run(1),
        delivery(2, retired="bat-1", retirement_type=RetirementType.HURT),
    ]
    assert wicket_count(rows, ODI) == 0
    assert retirement_type_for("bat-1", rows) is RetirementType.HURT
    assert retirement_type_for("nobody", rows) is None


def test_e11_retired_out_is_a_wicket() -> None:
    row = delivery(
        1,
        is_wicket=True,
        dismissal_type=DismissalType.RETIRED_OUT,
        dismissed="bat-1",
        credited=False,
    )
    assert wicket_count([row], ODI) == 1


def test_wicket_count_is_capped_at_ten_and_deduplicates() -> None:
    rows = [
        delivery(
            seq,
            is_wicket=True,
            dismissal_type=DismissalType.BOWLED,
            dismissed=f"p{seq}",
            credited=True,
        )
        for seq in range(1, 12)
    ]
    assert wicket_count(rows, ODI) == 10
    duplicate = [
        delivery(1, is_wicket=True, dismissal_type=DismissalType.BOWLED, dismissed="p"),
        delivery(2, is_wicket=True, dismissal_type=DismissalType.BOWLED, dismissed="p"),
    ]
    assert wicket_count(duplicate, ODI) == 1


def test_extras_breakdown() -> None:
    rows = [
        delivery(1, wides=1),
        delivery(2, noballs=1),
        delivery(3, byes=2),
        delivery(4, leg_byes=3),
        delivery(5, penalty=4),
    ]
    extras = extras_breakdown(rows, "v")
    assert (extras.wides, extras.noballs, extras.byes, extras.leg_byes, extras.penalty) == (
        1,
        1,
        2,
        3,
        4,
    )
    assert extras.total == 11
    assert extras.rule_version == "v"


def test_overs_display_and_completion() -> None:
    assert overs_display(7, HUNDRED) == "1.2"
    assert overs_display(100, HUNDRED) == "20.0"
    assert overs_display(0, ODI) == "0.0"
    assert over_is_complete(5, HUNDRED)
    assert not over_is_complete(4, HUNDRED)
    assert over_is_complete(6, ODI)
    with pytest.raises(InvalidDeliveryError):
        overs_display(-1, ODI)


def test_over_complete_after_six_legal_balls_even_with_wides() -> None:
    rows = [delivery(1, wides=1)] + [legal_run(seq) for seq in range(2, 8)]
    assert legal_balls(rows) == 6
    assert over_is_complete(legal_balls(rows), ODI)


def test_hundred_set_and_maiden_use_five_ball_unit() -> None:
    rows = [legal_run(seq) for seq in range(1, 6)]
    assert over_is_complete(legal_balls(rows), HUNDRED)
    summaries = overs_of(rows, HUNDRED, "hundred")
    assert len(summaries) == 1
    assert summaries[0].is_maiden
    assert summaries[0].is_complete
    assert summaries[0].legal_balls == 5
    assert maidens_of(rows, "bowl-1", HUNDRED, "hundred") == 1


def test_hundred_economy_is_runs_per_five_ball_set() -> None:
    rows = [legal_run(seq, runs=5) for seq in range(1, 11)]
    line = bowling_line("bowl-1", rows, HUNDRED, "hundred")
    assert line.economy == pytest.approx(25.0)
    assert line.legal_balls == 10


def test_hundred_innings_is_100_balls() -> None:
    assert HUNDRED.total_balls == HUNDRED.overs_limit * HUNDRED.balls_per_over  # type: ignore[operator]
    assert total_balls(HUNDRED) == 100


def test_overs_of_groups_and_rejects_two_bowlers() -> None:
    rows = [legal_run(1), legal_run(2), legal_run(7)]
    summaries = overs_of(rows, ODI, "v")
    assert [row.over_number for row in summaries] == [1, 2]
    with pytest.raises(InvalidDeliveryError):
        overs_of([legal_run(1), legal_run(2, bowler="other")], ODI, "v")


def test_batting_line_counts_and_rates() -> None:
    rows = [
        legal_run(1, 4),
        legal_run(2, 6),
        legal_run(3, 1),
        legal_run(4, 2),
        legal_run(5, 3),
        legal_run(6, 0),
        legal_run(7, 5),
        delivery(8, wides=1),
        delivery(9, noballs=1, runs=4),
    ]
    line = batting_line("bat-1", rows, ODI, "v")
    assert line.runs == 25
    assert line.balls == 8
    assert (line.fours, line.sixes, line.ones, line.twos, line.threes, line.fives) == (
        2,
        1,
        1,
        1,
        1,
        1,
    )
    assert line.dots == 1
    assert line.strike_rate == pytest.approx(312.5)
    assert line.dot_pct == pytest.approx(12.5)
    assert line.boundary_pct == pytest.approx(37.5)
    assert line.did_bat
    assert not line.out
    assert line.is_not_out


def test_batting_line_dismissal_fields() -> None:
    row = delivery(
        1,
        is_wicket=True,
        dismissal_type=DismissalType.CAUGHT,
        dismissed="bat-1",
        credited=True,
        fielder1="f1",
        fielder2="f2",
        fielder3="f3",
    )
    line = batting_line("bat-1", [row], ODI, "v")
    assert line.out
    assert line.dismissal_type is DismissalType.CAUGHT
    assert line.bowler_id == "bowl-1"
    assert (line.fielder1_id, line.fielder2_id, line.fielder3_id) == ("f1", "f2", "f3")
    assert dismissal_for("bat-1", [row]) is row
    assert dismissal_for("nobody", [row]) is None


def test_batting_line_zero_balls_yields_none_rates() -> None:
    line = batting_line("bat-1", [], ODI, "v")
    assert line.strike_rate is None and line.dot_pct is None and line.boundary_pct is None


def test_bowling_line_accounting() -> None:
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
    line = bowling_line("bowl-1", rows, ODI, "v")
    assert line.legal_balls == 6
    assert line.runs_conceded == 11
    assert line.wickets == 1
    assert line.wides == 1 and line.noballs == 1
    assert line.economy == pytest.approx(11.0)
    assert line.bowling_avg == pytest.approx(11.0)
    assert line.strike_rate == pytest.approx(6.0)


def test_bowling_line_helpers() -> None:
    assert is_batter_dot(delivery(1))
    assert not is_batter_dot(delivery(1, byes=1))
    assert is_bowling_dot(delivery(1))
    assert not is_bowling_dot(delivery(1, byes=1))
    assert batter_appearances([delivery(1, batter="a", non_striker="b")]) == ("a", "b")
    assert bowler_appearances([delivery(1, bowler="x")]) == ("x",)


def test_rate_helpers() -> None:
    assert dot_pct(2, 8) == 25.0
    assert dot_pct(0, 0) is None
    assert boundary_pct(2, 1, 8) == 37.5
    assert boundary_pct(0, 0, 0) is None
    assert overs_from_balls(7, HUNDRED) == 1.4
    assert balls_remaining(100, 7) == 93
    assert balls_remaining(None, 7) is None
    assert balls_remaining(5, 7) == 0


def test_run_rate_and_required_rate() -> None:
    assert current_run_rate(60, 60, ODI) == 6.0
    assert required_run_rate(100, 40, 120, 60, ODI) == 6.0
    assert required_run_rate(100, 40, 60, 60, ODI) is None
    assert required_run_rate(100, 40, None, 60, ODI) is None


def test_nrr_overs_full_quota_when_all_out() -> None:
    assert nrr_overs(100, all_out=True, overs_limit=50, config=ODI) == 50.0
    assert nrr_overs(100, all_out=False, overs_limit=50, config=ODI) == pytest.approx(100 / 6)
    assert nrr_overs(100, all_out=True, overs_limit=None, config=ODI) == pytest.approx(100 / 6)
    no_quota = RuleConfig(version="v", nrr_full_quota_if_all_out=False)
    assert nrr_overs(100, all_out=True, overs_limit=50, config=no_quota) == pytest.approx(100 / 6)
