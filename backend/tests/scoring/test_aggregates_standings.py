"""Career aggregates, team NRR and tournament standings."""

from __future__ import annotations

import pytest
from tests.scoring.helpers import TEAM_A, TEAM_B, innings, legal_run, result

from apps.scoring.aggregates import (
    career_totals,
    net_run_rate,
    player_match_line,
    season_totals,
    team_match_line,
)
from apps.scoring.config import RuleConfig, preset_for_format
from apps.scoring.types import (
    BattingLine,
    BowlingLine,
    Format,
    PlayerMatchLine,
    ResultType,
    TeamMatchLine,
)

ODI = preset_for_format(Format.ODI)
T20I = preset_for_format(Format.T20I)


def _batting(
    runs: int, balls: int, *, out: bool, match_id: str = "m", version: str = "v"
) -> BattingLine:
    return BattingLine(
        rule_version=version,
        player_id="p1",
        did_bat=True,
        runs=runs,
        balls=balls,
        fours=runs // 4,
        sixes=0,
        dots=0,
        out=out,
        strike_rate=100.0 * runs / balls if balls else None,
    )


def _bowling(wickets: int, runs: int, balls: int) -> BowlingLine:
    return BowlingLine(
        rule_version="v",
        player_id="p1",
        legal_balls=balls,
        runs_conceded=runs,
        wickets=wickets,
        economy=runs * 6 / balls if balls else None,
    )


def _player_line(
    match_id: str,
    *,
    batting: tuple[BattingLine, ...] = (),
    bowling: tuple[BowlingLine, ...] = (),
    super_over: bool = False,
) -> PlayerMatchLine:
    return PlayerMatchLine(
        rule_version="v",
        match_id=match_id,
        player_id="p1",
        team_id=TEAM_A,
        format=Format.ODI,
        did_bat=bool(batting),
        did_bowl=bool(bowling),
        batting=batting,
        bowling=bowling,
        is_super_over=super_over,
    )


def test_career_totals_and_undefined_average() -> None:
    lines = [
        _player_line(
            "m1",
            batting=(_batting(60, 40, out=False),),
            bowling=(_bowling(2, 30, 24),),
        ),
        _player_line(
            "m2",
            batting=(_batting(100, 80, out=True),),
            bowling=(_bowling(5, 20, 30),),
        ),
    ]
    totals = career_totals("p1", lines, ODI)
    assert totals.matches == 2
    assert totals.innings == 2
    assert totals.not_outs == 1
    assert totals.runs == 160
    assert totals.balls_faced == 120
    assert totals.high_score == 100
    assert totals.fifties == 1
    assert totals.hundreds == 1
    assert totals.batting_avg == pytest.approx(160.0)
    assert totals.strike_rate == pytest.approx(160 / 120 * 100)
    assert totals.wickets == 7
    assert totals.five_wicket_hauls == 1
    assert (totals.best_wickets, totals.best_runs) == (5, 20)
    assert totals.bowling_avg == pytest.approx(50 / 7)
    assert totals.economy == pytest.approx(50 * 6 / 54)
    assert totals.bowling_sr == pytest.approx(54 / 7)


def test_career_average_undefined_when_never_dismissed() -> None:
    totals = career_totals("p1", [_player_line("m1", batting=(_batting(50, 30, out=False),))], ODI)
    assert totals.batting_avg is None
    assert totals.bowling_avg is None
    assert totals.economy is None


def test_high_score_prefers_not_out_on_a_tie() -> None:
    lines = [
        _player_line("m1", batting=(_batting(100, 80, out=True),)),
        _player_line("m2", batting=(_batting(100, 70, out=False),)),
    ]
    totals = career_totals("p1", lines, ODI)
    assert totals.high_score == 100
    assert totals.high_score_not_out is True


def test_career_excludes_super_over_by_default() -> None:
    lines = [
        _player_line("m1", batting=(_batting(40, 20, out=True),)),
        _player_line(
            "m2",
            batting=(_batting(20, 5, out=False),),
            super_over=True,
        ),
    ]
    assert career_totals("p1", lines, ODI).matches == 1
    assert career_totals("p1", lines, ODI).runs == 40
    include = RuleConfig(version="v", include_super_over_in_stats=True)
    assert career_totals("p1", lines, include).matches == 2
    assert career_totals("p1", lines, include).runs == 60


def test_player_match_line_marks_pure_super_over_matches() -> None:
    from apps.scoring.engine import rebuild_innings

    super_over = rebuild_innings(
        innings([legal_run(1, 10)], is_super_over=True, super_over_number=1), ODI
    )
    excluded = player_match_line("m", "bat-1", TEAM_A, Format.ODI, [super_over], ODI)
    assert excluded.is_super_over is True
    assert excluded.did_bat is False  # super over excluded by default
    include = RuleConfig(version="v", include_super_over_in_stats=True)
    included = player_match_line("m", "bat-1", TEAM_A, Format.ODI, [super_over], include)
    assert included.did_bat is True
    assert included.runs == 10


def test_career_empty() -> None:
    totals = career_totals("p1", [], ODI)
    assert totals.matches == 0
    assert totals.high_score is None
    assert totals.best_wickets is None


def test_player_match_line_collects_each_innings() -> None:
    # Inn 1: bat-1 bats. Inn 2: bat-1 bowls.
    context1 = innings([legal_run(1, 4)], innings_number=1)
    context2 = innings(
        [legal_run(1, 2, batter="bat-9", non_striker="bat-8", bowler="bat-1")],
        innings_number=3,
    )
    from apps.scoring.engine import rebuild_innings

    built1 = rebuild_innings(context1, ODI)
    built2 = rebuild_innings(context2, ODI)
    line = player_match_line("m", "bat-1", TEAM_A, Format.ODI, [built1, built2], ODI)
    assert line.did_bat and line.did_bowl
    assert len(line.batting) == 1
    assert len(line.bowling) == 1
    assert line.innings_batted == 1
    assert line.runs == 4
    assert line.wickets == 0


def test_team_line_and_nrr_full_quota_when_all_out() -> None:
    line = TeamMatchLine(
        rule_version="v",
        match_id="m",
        team_id=TEAM_A,
        format=Format.ODI,
        runs_for=200,
        balls_faced=100,
        all_out=True,
        overs_limit=50,
        runs_against=150,
        balls_bowled=300,
        opponent_all_out=False,
    )
    assert net_run_rate(TEAM_A, [line], ODI) == pytest.approx(1.0)


def test_nrr_ignores_no_result_and_super_over() -> None:
    good = TeamMatchLine(
        rule_version="v",
        match_id="m1",
        team_id=TEAM_A,
        format=Format.ODI,
        runs_for=300,
        balls_faced=300,
        runs_against=250,
        balls_bowled=300,
        overs_limit=50,
    )
    abandoned = TeamMatchLine(
        rule_version="v",
        match_id="m2",
        team_id=TEAM_A,
        format=Format.ODI,
        runs_for=999,
        balls_faced=10,
        runs_against=0,
        balls_bowled=10,
        is_no_result=True,
    )
    super_over = TeamMatchLine(
        rule_version="v",
        match_id="m3",
        team_id=TEAM_A,
        format=Format.ODI,
        runs_for=999,
        balls_faced=5,
        runs_against=0,
        balls_bowled=5,
        is_super_over=True,
    )
    assert net_run_rate(TEAM_A, [good, abandoned, super_over], ODI) == pytest.approx(1.0)
    include = RuleConfig(version="v", include_super_over_in_nrr=True)
    assert net_run_rate(TEAM_A, [super_over], include) == pytest.approx(999 / (5 / 6))


def test_nrr_none_when_no_overs() -> None:
    line = TeamMatchLine(
        rule_version="v",
        match_id="m",
        team_id=TEAM_A,
        format=Format.ODI,
        runs_for=0,
        balls_faced=0,
        runs_against=0,
        balls_bowled=0,
    )
    assert net_run_rate(TEAM_A, [line], ODI) is None
    assert net_run_rate(TEAM_A, [], ODI) is None


def test_team_match_line_builds_from_innings() -> None:
    first = result(TEAM_A, 300, legal=300)
    second = result(TEAM_B, 250, all_out=True, legal=240)
    line = team_match_line(
        "m",
        TEAM_A,
        Format.ODI,
        [first, second],
        ODI,
        result_type=ResultType.WIN,
        winner_team_id=TEAM_A,
    )
    assert line.runs_for == 300
    assert line.runs_against == 250
    assert line.opponent_all_out is True
    assert line.won is True
    assert line.overs_limit == 50


def _team_line(
    team: str,
    *,
    result_type: ResultType | None,
    won: bool = False,
    runs: int = 250,
    against: int = 240,
    no_result: bool = False,
    super_over: bool = False,
) -> TeamMatchLine:
    return TeamMatchLine(
        rule_version="v",
        match_id=f"{team}-{result_type}-{won}-{super_over}",
        team_id=team,
        format=Format.ODI,
        runs_for=runs,
        balls_faced=300,
        runs_against=against,
        balls_bowled=300,
        overs_limit=50,
        result_type=result_type,
        won=won,
        is_no_result=no_result,
        is_super_over=super_over,
    )


def test_season_totals_counts_every_outcome() -> None:
    lines = [
        _team_line(TEAM_A, result_type=ResultType.WIN, won=True),
        _team_line(TEAM_A, result_type=ResultType.WIN, won=False),
        _team_line(TEAM_A, result_type=ResultType.TIE),
        _team_line(TEAM_A, result_type=ResultType.DRAW),
        _team_line(TEAM_A, result_type=ResultType.NO_RESULT, no_result=True),
        _team_line(TEAM_A, result_type=None),
        _team_line(TEAM_A, result_type=ResultType.SUPER_OVER_TIE_BREAK, won=True, super_over=True),
    ]
    totals = season_totals(TEAM_A, lines, ODI)
    assert totals.matches == 5
    assert (totals.won, totals.lost, totals.tied, totals.drawn, totals.no_result) == (
        1,
        1,
        1,
        1,
        1,
    )
    assert totals.points == 5
    assert totals.runs_for == 1250
    assert totals.nrr is not None


def test_season_totals_ignores_other_teams() -> None:
    totals = season_totals(TEAM_A, [_team_line(TEAM_B, result_type=ResultType.WIN)], ODI)
    assert totals.matches == 0 and totals.points == 0


def test_standings_order_by_points_then_nrr_then_id() -> None:
    lines = [
        _team_line(TEAM_A, result_type=ResultType.WIN, won=True),
        _team_line(TEAM_B, result_type=ResultType.WIN, won=True),
        _team_line("team-c", result_type=ResultType.TIE),
    ]
    from apps.scoring.standings import build_standings, points_table

    rows = build_standings([TEAM_A, TEAM_B, "team-c"], lines, ODI)
    assert [row.team_id for row in rows] == [TEAM_A, TEAM_B, "team-c"]
    assert [row.position for row in rows] == [1, 2, 3]
    assert rows[0].points == 2 and rows[2].points == 1
    assert points_table([TEAM_A], lines, ODI)[0].team_id == TEAM_A


def test_standings_deduplicates_team_ids_and_handles_no_nrr() -> None:
    lines = [
        _team_line(TEAM_A, result_type=ResultType.NO_RESULT, no_result=True),
        TeamMatchLine(
            rule_version="v",
            match_id="x",
            team_id="team-z",
            format=Format.ODI,
            result_type=ResultType.WIN,
            won=True,
            runs_for=10,
            balls_faced=0,
            runs_against=10,
            balls_bowled=0,
        ),
    ]
    from apps.scoring.standings import build_standings

    rows = build_standings([TEAM_A, TEAM_A, "team-z"], lines, ODI)
    assert len(rows) == 2
    assert [row.team_id for row in rows] == ["team-z", TEAM_A]
    assert rows[0].nrr is None


def test_standings_equal_points_break_on_nrr() -> None:
    lines = [
        _team_line(TEAM_A, result_type=ResultType.WIN, won=True, runs=250, against=240),  # nrr 0.2
        _team_line(TEAM_B, result_type=ResultType.WIN, won=True, runs=260, against=240),  # nrr 0.4
        _team_line("team-c", result_type=ResultType.WIN, won=True, runs=240, against=240),
    ]
    from apps.scoring.standings import build_standings

    rows = build_standings([TEAM_A, TEAM_B, "team-c"], lines, ODI)
    assert [row.team_id for row in rows] == [TEAM_B, TEAM_A, "team-c"]
    assert [row.points for row in rows] == [2, 2, 2]
    assert [round(row.nrr, 4) for row in rows] == [0.4, 0.2, 0.0]


def test_standings_identical_points_and_nrr_break_on_team_id() -> None:
    lines = [
        _team_line(TEAM_A, result_type=ResultType.WIN, won=True, runs=250, against=240),
        _team_line(TEAM_B, result_type=ResultType.WIN, won=True, runs=250, against=240),
        _team_line("team-c", result_type=ResultType.WIN, won=False),  # a loss
    ]
    from apps.scoring.standings import build_standings

    rows = build_standings(["team-c", TEAM_B, TEAM_A], lines, ODI)
    assert [row.team_id for row in rows] == [TEAM_A, TEAM_B, "team-c"]
    assert [row.position for row in rows] == [1, 2, 3]
    a, b, c = rows
    assert (a.won, a.lost, a.tied, a.drawn, a.no_result, a.points) == (1, 0, 0, 0, 0, 2)
    assert (b.won, b.lost, b.tied, b.drawn, b.no_result, b.points) == (1, 0, 0, 0, 0, 2)
    assert (c.won, c.lost, c.tied, c.drawn, c.no_result, c.points) == (0, 1, 0, 0, 0, 0)


def test_standings_row_reports_full_outcome_counts() -> None:
    lines = [
        _team_line(TEAM_A, result_type=ResultType.WIN, won=True),
        _team_line(TEAM_A, result_type=ResultType.WIN, won=False),
        _team_line(TEAM_A, result_type=ResultType.TIE),
        _team_line(TEAM_A, result_type=ResultType.DRAW),
        _team_line(TEAM_A, result_type=ResultType.NO_RESULT, no_result=True),
    ]
    from apps.scoring.standings import build_standings

    rows = build_standings([TEAM_A], lines, ODI)
    assert len(rows) == 1
    row = rows[0]
    assert row.rule_version == ODI.version
    assert row.position == 1 and row.team_id == TEAM_A
    assert (row.played, row.won, row.lost, row.tied, row.drawn, row.no_result, row.points) == (
        5,
        1,
        1,
        1,
        1,
        1,
        5,
    )


def test_standings_no_nrr_ranks_below_positive_nrr_at_equal_points() -> None:
    zero_balls_win = TeamMatchLine(
        rule_version="v",
        match_id="nb",
        team_id="no-balls",
        format=Format.ODI,
        result_type=ResultType.WIN,
        won=True,
        runs_for=10,
        balls_faced=0,
        runs_against=10,
        balls_bowled=0,
        overs_limit=50,
    )
    lines = [
        zero_balls_win,
        _team_line(TEAM_A, result_type=ResultType.WIN, won=True, runs=250, against=240),
    ]
    from apps.scoring.standings import build_standings

    rows = build_standings([TEAM_A, "no-balls"], lines, ODI)
    assert [row.team_id for row in rows] == [TEAM_A, "no-balls"]
    assert round(rows[0].nrr, 4) == 0.2 and rows[1].nrr is None
