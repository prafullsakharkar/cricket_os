"""Career and team aggregates built from durable per-match lines (Tier 2)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from apps.scoring.config import RuleConfig
from apps.scoring.laws.rates import (
    batting_average,
    bowling_average,
    bowling_strike_rate,
    economy,
    nrr_overs,
    safe_div,
    strike_rate,
)
from apps.scoring.types import (
    BattingLine,
    BowlingLine,
    CareerTotals,
    Format,
    InningsResult,
    PlayerMatchLine,
    ResultType,
    SeasonTotals,
    TeamMatchLine,
)


def _included(result: InningsResult, config: RuleConfig) -> bool:
    return config.include_super_over_in_stats or not result.is_super_over


def player_match_line(
    match_id: str,
    player_id: str,
    team_id: str,
    fmt: Format,
    innings: Sequence[InningsResult],
    config: RuleConfig,
) -> PlayerMatchLine:
    """Combine a player's innings in one match (Test: two innings)."""

    batting: list[BattingLine] = []
    bowling: list[BowlingLine] = []
    for result in innings:
        if result.batting_team_id != team_id or not _included(result, config):
            continue
        batting.extend(line for line in result.batting if line.player_id == player_id)
        bowling.extend(line for line in result.bowling if line.player_id == player_id)
    return PlayerMatchLine(
        rule_version=config.version,
        match_id=match_id,
        player_id=player_id,
        team_id=team_id,
        format=fmt,
        did_bat=bool(batting),
        did_bowl=bool(bowling),
        batting=tuple(batting),
        bowling=tuple(bowling),
        is_super_over=bool(innings) and all(row.is_super_over for row in innings),
    )


def career_totals(
    player_id: str,
    lines: Iterable[PlayerMatchLine],
    config: RuleConfig,
) -> CareerTotals:
    """Career/season totals with standard (undefined-safe) averages."""

    selected = [
        line for line in lines if not line.is_super_over or config.include_super_over_in_stats
    ]
    batting = [row for line in selected for row in line.batting if row.did_bat]
    bowling = [row for line in selected for row in line.bowling]

    runs = sum(row.runs for row in batting)
    balls_faced = sum(row.balls for row in batting)
    innings = len(batting)
    not_outs = sum(1 for row in batting if row.is_not_out)
    fours = sum(row.fours for row in batting)
    sixes = sum(row.sixes for row in batting)
    high = max(batting, key=lambda row: (row.runs, row.is_not_out), default=None)

    balls_bowled = sum(row.legal_balls for row in bowling)
    runs_conceded = sum(row.runs_conceded for row in bowling)
    wickets = sum(row.wickets for row in bowling)
    maidens = sum(row.maidens for row in bowling)
    best = max(bowling, key=lambda row: (row.wickets, -row.runs_conceded), default=None)

    return CareerTotals(
        rule_version=config.version,
        player_id=player_id,
        matches=len({line.match_id for line in selected}),
        innings=innings,
        not_outs=not_outs,
        runs=runs,
        balls_faced=balls_faced,
        high_score=None if high is None else high.runs,
        high_score_not_out=False if high is None else high.is_not_out,
        fifties=sum(1 for row in batting if 50 <= row.runs < 100),
        hundreds=sum(1 for row in batting if row.runs >= 100),
        fours=fours,
        sixes=sixes,
        batting_avg=batting_average(runs, innings, not_outs),
        strike_rate=strike_rate(runs, balls_faced),
        balls_bowled=balls_bowled,
        runs_conceded=runs_conceded,
        wickets=wickets,
        maidens=maidens,
        best_wickets=None if best is None else best.wickets,
        best_runs=None if best is None else best.runs_conceded,
        five_wicket_hauls=sum(1 for row in bowling if row.wickets >= 5),
        bowling_avg=bowling_average(runs_conceded, wickets),
        economy=economy(runs_conceded, balls_bowled, config),
        bowling_sr=bowling_strike_rate(balls_bowled, wickets),
    )


def team_match_line(
    match_id: str,
    team_id: str,
    fmt: Format,
    innings: Sequence[InningsResult],
    config: RuleConfig,
    *,
    overs_limit: int | None = None,
    result_type: ResultType | None = None,
    winner_team_id: str | None = None,
    super_over: bool = False,
    is_no_result: bool = False,
) -> TeamMatchLine:
    """One team's match contribution, in the shape points/NRR need."""

    rows = [row for row in innings if row.is_super_over == super_over]
    for_batting = [row for row in rows if row.batting_team_id == team_id]
    against_batting = [row for row in rows if row.bowling_team_id == team_id]
    return TeamMatchLine(
        rule_version=config.version,
        match_id=match_id,
        team_id=team_id,
        format=fmt,
        runs_for=sum(row.totals.runs for row in for_batting),
        balls_faced=sum(row.totals.legal_balls for row in for_batting),
        all_out=any(row.all_out for row in for_batting),
        overs_limit=config.overs_limit if overs_limit is None else overs_limit,
        runs_against=sum(row.totals.runs for row in against_batting),
        balls_bowled=sum(row.totals.legal_balls for row in against_batting),
        opponent_all_out=any(row.all_out for row in against_batting),
        result_type=result_type,
        won=winner_team_id == team_id,
        is_super_over=super_over,
        is_no_result=is_no_result,
    )


def net_run_rate(
    team_id: str,
    lines: Iterable[TeamMatchLine],
    config: RuleConfig,
) -> float | None:
    """Tournament NRR with full-quota charging for bowled-out sides (E14)."""

    selected = [
        line
        for line in lines
        if not line.is_no_result and (config.include_super_over_in_nrr or not line.is_super_over)
    ]
    if not selected:
        return None
    overs_faced = sum(
        nrr_overs(
            line.balls_faced,
            all_out=line.all_out,
            overs_limit=line.overs_limit,
            config=config,
        )
        for line in selected
    )
    overs_bowled = sum(
        nrr_overs(
            line.balls_bowled,
            all_out=line.opponent_all_out,
            overs_limit=line.overs_limit,
            config=config,
        )
        for line in selected
    )
    runs_for = sum(line.runs_for for line in selected)
    runs_against = sum(line.runs_against for line in selected)
    scored = safe_div(runs_for, overs_faced)
    conceded = safe_div(runs_against, overs_bowled)
    if scored is None or conceded is None:
        return None
    return scored - conceded


def season_totals(
    team_id: str,
    lines: Iterable[TeamMatchLine],
    config: RuleConfig,
) -> SeasonTotals:
    """Points-table totals for one team across a season."""

    selected = [
        line
        for line in lines
        if line.team_id == team_id
        and (config.include_super_over_in_stats or not line.is_super_over)
    ]
    won = lost = tied = drawn = no_result = points = 0
    played = 0
    counted = [line for line in selected if line.result_type is not None]
    for line in counted:
        played += 1
        if line.is_no_result or line.result_type in {
            ResultType.NO_RESULT,
            ResultType.ABANDONED,
            ResultType.CANCELLED,
        }:
            no_result += 1
            points += config.points_no_result
        elif line.result_type is ResultType.TIE:
            tied += 1
            points += config.points_tie
        elif line.result_type is ResultType.DRAW:
            drawn += 1
            points += config.points_draw
        elif line.result_type in {
            ResultType.WIN,
            ResultType.SUPER_OVER_TIE_BREAK,
            ResultType.FORFEIT,
        }:
            if line.won:
                won += 1
                points += config.points_win
            else:
                lost += 1
                points += config.points_loss
    return SeasonTotals(
        rule_version=config.version,
        team_id=team_id,
        matches=played,
        won=won,
        lost=lost,
        tied=tied,
        drawn=drawn,
        no_result=no_result,
        points=points,
        runs_for=sum(line.runs_for for line in counted),
        balls_faced=sum(line.balls_faced for line in counted),
        runs_against=sum(line.runs_against for line in counted),
        balls_bowled=sum(line.balls_bowled for line in counted),
        nrr=net_run_rate(team_id, counted, config),
    )
