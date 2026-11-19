"""Match completion, result precedence, margins and Super Over tie-breaks."""

from __future__ import annotations

from collections.abc import Sequence

from apps.scoring.config import RuleConfig
from apps.scoring.errors import MissingDlsTargetError
from apps.scoring.types import (
    InningsResult,
    MarginType,
    MatchContext,
    MatchResult,
    MatchStatus,
    ResultType,
)


def quota_balls(innings: InningsResult, match: MatchContext, config: RuleConfig) -> int | None:
    """The innings quota in balls; reduced/DLS overs come from the match context."""

    limit = match.overs_limit if match.overs_limit is not None else config.overs_limit
    return None if limit is None else limit * config.balls_per_over


def _win(
    match: MatchContext,
    result_type: ResultType,
    winner: str | None,
    margin_type: MarginType,
    margin_value: int | None,
    *,
    is_dls: bool,
    is_super_over: bool = False,
) -> MatchResult:
    text = None
    if winner is not None and margin_value is not None:
        if margin_type is MarginType.WICKETS:
            text = f"won by {margin_value} wicket{'s' if margin_value != 1 else ''}"
        elif margin_type is MarginType.RUNS:
            text = f"won by {margin_value} run{'s' if margin_value != 1 else ''}"
        elif margin_type is MarginType.INNINGS:
            text = f"won by an innings and {margin_value} run{'s' if margin_value != 1 else ''}"
    return MatchResult(
        rule_version="",
        match_id=match.match_id,
        result_type=result_type,
        winner_team_id=winner,
        margin_type=margin_type,
        margin_value=margin_value,
        is_dls=is_dls,
        is_super_over=is_super_over,
        result_text=text,
    )


def _with_version(result: MatchResult, config: RuleConfig) -> MatchResult:
    return MatchResult(
        rule_version=config.version,
        match_id=result.match_id,
        result_type=result.result_type,
        winner_team_id=result.winner_team_id,
        margin_type=result.margin_type,
        margin_value=result.margin_value,
        is_dls=result.is_dls,
        is_super_over=result.is_super_over,
        result_text=result.result_text,
    )


def _limited_overs_result(
    match: MatchContext,
    innings: Sequence[InningsResult],
    config: RuleConfig,
) -> MatchResult | None:
    if len(innings) < 2:
        if match.status is MatchStatus.COMPLETED:
            return _win(match, ResultType.NO_RESULT, None, MarginType.NONE, None, is_dls=False)
        return None

    first, last = innings[0], innings[-1]
    if match.is_dls_applied:
        if last.dls_target is None:
            raise MissingDlsTargetError(
                "DLS target must be supplied by the provider/licensed source",
                innings_id=last.innings_id,
            )
        target = last.dls_target
    else:
        target = first.totals.runs + 1

    if last.totals.runs >= target:
        wickets_left = max(config.max_wickets - last.totals.wickets, 0)
        return _win(
            match,
            ResultType.WIN,
            last.batting_team_id,
            MarginType.WICKETS,
            wickets_left,
            is_dls=match.is_dls_applied,
        )

    quota = quota_balls(last, match, config)
    done = last.all_out or (quota is not None and last.totals.legal_balls >= quota)
    if done:
        if last.totals.runs == first.totals.runs:
            return _win(
                match,
                ResultType.TIE,
                None,
                MarginType.TIE,
                None,
                is_dls=match.is_dls_applied,
            )
        if last.totals.runs < first.totals.runs:
            return _win(
                match,
                ResultType.WIN,
                first.batting_team_id,
                MarginType.RUNS,
                first.totals.runs - last.totals.runs,
                is_dls=match.is_dls_applied,
            )
    if match.status is MatchStatus.COMPLETED:
        return _win(
            match,
            ResultType.NO_RESULT,
            None,
            MarginType.NONE,
            None,
            is_dls=match.is_dls_applied,
        )
    return None


def _aggregate(innings: Sequence[InningsResult], team_id: str) -> tuple[int, int]:
    rows = [row for row in innings if row.batting_team_id == team_id and not row.is_super_over]
    return sum(row.totals.runs for row in rows), len(rows)


def _test_result(
    match: MatchContext,
    innings: Sequence[InningsResult],
    config: RuleConfig,
) -> MatchResult | None:
    if len(innings) < 2:
        if match.status is MatchStatus.COMPLETED:
            return _win(match, ResultType.NO_RESULT, None, MarginType.NONE, None, is_dls=False)
        return None

    last = innings[-1]
    last_team = last.batting_team_id
    opponent = next(team for team in match.teams if team != last_team)
    previous_runs, _ = _aggregate(innings[:-1], last_team)
    opponent_runs, _ = _aggregate(innings[:-1], opponent)
    target = opponent_runs - previous_runs + 1

    if len(innings) >= 4 and target > 0 and last.totals.runs >= target:
        wickets_left = max(config.max_wickets - last.totals.wickets, 0)
        return _win(
            match,
            ResultType.WIN,
            last_team,
            MarginType.WICKETS,
            wickets_left,
            is_dls=False,
        )

    if match.time_expired and config.allows_draw:
        return _win(match, ResultType.DRAW, None, MarginType.NONE, None, is_dls=False)

    quota = quota_balls(last, match, config)
    last_done = last.all_out or (quota is not None and last.totals.legal_balls >= quota)
    if last_done or match.status is MatchStatus.COMPLETED:
        last_total, last_innings = _aggregate(innings, last_team)
        opponent_total, opponent_innings = _aggregate(innings, opponent)
        if last_total == opponent_total:
            return _win(match, ResultType.TIE, None, MarginType.TIE, None, is_dls=False)
        winner = last_team if last_total > opponent_total else opponent
        winner_total = max(last_total, opponent_total)
        loser_total = min(last_total, opponent_total)
        winner_innings = last_innings if winner == last_team else opponent_innings
        loser_innings = opponent_innings if winner == last_team else last_innings
        if winner_innings < loser_innings:
            return _win(
                match,
                ResultType.WIN,
                winner,
                MarginType.INNINGS,
                winner_total - loser_total,
                is_dls=False,
            )
        return _win(
            match,
            ResultType.WIN,
            winner,
            MarginType.RUNS,
            winner_total - loser_total,
            is_dls=False,
        )
    return None


def _super_over_result(
    match: MatchContext,
    super_overs: Sequence[InningsResult],
    config: RuleConfig,
) -> MatchResult | None:
    by_number: dict[int, list[InningsResult]] = {}
    for innings in super_overs:
        by_number.setdefault(innings.super_over_number or 1, []).append(innings)
    for number in sorted(by_number):
        pair = by_number[number]
        if len(pair) < 2:
            return None
        first, last = pair[0], pair[1]
        if first.totals.runs == last.totals.runs:
            continue
        winner = (
            last.batting_team_id if last.totals.runs > first.totals.runs else first.batting_team_id
        )
        margin = abs(last.totals.runs - first.totals.runs)
        return _win(
            match,
            ResultType.SUPER_OVER_TIE_BREAK,
            winner,
            MarginType.RUNS,
            margin,
            is_dls=False,
            is_super_over=True,
        )
    # Every supplied Super Over was tied: no boundary-count fallback is applied
    # unless a licensed/competition rule supplies one.
    return _win(
        match,
        ResultType.SUPER_OVER_TIE_BREAK,
        None,
        MarginType.TIE,
        None,
        is_dls=False,
        is_super_over=True,
    )


def decide_match_result(
    match: MatchContext,
    innings: Sequence[InningsResult],
    config: RuleConfig,
) -> MatchResult | None:
    """Decide a match result, or ``None`` while it is still in progress."""

    if match.status in {MatchStatus.ABANDONED, MatchStatus.CANCELLED, MatchStatus.POSTPONED}:
        return _with_version(
            _win(match, ResultType.NO_RESULT, None, MarginType.NONE, None, is_dls=False),
            config,
        )
    if match.status is MatchStatus.FORFEITED:
        return _with_version(
            _win(match, ResultType.FORFEIT, None, MarginType.NONE, None, is_dls=False),
            config,
        )
    if match.time_expired and config.allows_draw and match.status is MatchStatus.COMPLETED:
        return _with_version(
            _win(match, ResultType.DRAW, None, MarginType.NONE, None, is_dls=False),
            config,
        )

    super_overs = [row for row in innings if row.is_super_over]
    regulation = [row for row in innings if not row.is_super_over]
    if not regulation:
        if match.status is MatchStatus.COMPLETED:
            return _with_version(
                _win(match, ResultType.NO_RESULT, None, MarginType.NONE, None, is_dls=False),
                config,
            )
        return None

    if config.max_innings == 4 and match.format.value in {"TEST", "FIRST_CLASS"}:
        result = _test_result(match, regulation, config)
    else:
        result = _limited_overs_result(match, regulation, config)
    if result is None or result.result_type is not ResultType.TIE:
        return None if result is None else _with_version(result, config)

    if config.super_over_enabled and super_overs:
        supers = _super_over_result(match, super_overs, config)
        if supers is not None:
            return _with_version(supers, config)
    return _with_version(result, config)


def decided_result(
    match: MatchContext,
    innings: Sequence[InningsResult],
    config: RuleConfig,
) -> MatchResult | None:
    """Alias kept for callers that prefer the explicit verb."""

    return decide_match_result(match, innings, config)
