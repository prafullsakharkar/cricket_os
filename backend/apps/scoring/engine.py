"""Deterministic innings and match rebuild from current deliveries.

The engine is a pure function of ``(deliveries, events, RuleConfig)``. It does
not read the clock, the network or the database, and never mutates its inputs.
"""

from __future__ import annotations

from collections.abc import Sequence

from apps.scoring.config import RuleConfig
from apps.scoring.errors import InvalidInningsError
from apps.scoring.laws import rates
from apps.scoring.laws.dismissals import (
    batter_appearances,
    batting_line,
    bowler_appearances,
    bowling_line,
    wicket_count,
)
from apps.scoring.laws.extras import extras_breakdown
from apps.scoring.laws.overs import legal_balls, overs_display, overs_of
from apps.scoring.partnerships import build_partnerships, innings_retirements
from apps.scoring.types import (
    InningsContext,
    InningsResult,
    InningsStatus,
    MatchContext,
    MatchResult,
    MatchScorecard,
    Totals,
)
from apps.scoring.validation import ordered_deliveries, ordered_events


def rebuild_innings(
    context: InningsContext,
    config: RuleConfig,
    *,
    estimated: bool = False,
) -> InningsResult:
    """Rebuild totals, scorecard, partnerships, FOW and overs for one innings."""

    deliveries = ordered_deliveries(context.deliveries, innings_id=context.innings_id)
    events = ordered_events(context.events, innings_id=context.innings_id)

    wickets = wicket_count(deliveries, config)
    all_out = context.all_out or wickets >= config.max_wickets
    if context.declared:
        status = InningsStatus.DECLARED
    elif all_out:
        status = InningsStatus.ALL_OUT
    else:
        status = context.status

    legal = legal_balls(deliveries)
    runs = sum(delivery.total_runs for delivery in deliveries)
    extras = extras_breakdown(deliveries, config.version)
    totals = Totals(
        rule_version=config.version,
        runs=runs,
        wickets=wickets,
        legal_balls=legal,
        extras=extras,
        overs_display=overs_display(legal, config),
        run_rate=rates.current_run_rate(runs, legal, config),
        estimated=estimated,
    )

    batting = tuple(
        batting_line(player_id, deliveries, config, config.version, estimated=estimated)
        for player_id in batter_appearances(deliveries)
    )
    bowling = tuple(
        bowling_line(player_id, deliveries, config, config.version, estimated=estimated)
        for player_id in bowler_appearances(deliveries)
    )
    partnerships, fows = build_partnerships(context, config)
    overs = overs_of(deliveries, config, config.version)
    retirements = innings_retirements(deliveries, events, config.version)

    return InningsResult(
        rule_version=config.version,
        innings_id=context.innings_id,
        innings_number=context.innings_number,
        batting_team_id=context.batting_team_id,
        bowling_team_id=context.bowling_team_id,
        totals=totals,
        batting=batting,
        bowling=bowling,
        fall_of_wickets=fows,
        partnerships=partnerships,
        overs=overs,
        retirements=retirements,
        is_super_over=context.is_super_over,
        super_over_number=context.super_over_number,
        is_follow_on_innings=context.is_follow_on_innings,
        declared=context.declared,
        all_out=all_out,
        status=status,
        dls_target=context.dls_target,
        estimated=estimated,
    )


def rebuild_match(
    match: MatchContext,
    innings: Sequence[InningsContext],
    config: RuleConfig,
    *,
    estimated: bool = False,
) -> MatchScorecard:
    """Rebuild every innings and decide the match result.

    ``results`` is imported lazily so the pure modules stay acyclic.
    """

    if len(innings) > config.max_innings:
        raise InvalidInningsError(
            f"format allows at most {config.max_innings} innings, got {len(innings)}"
        )
    results = tuple(rebuild_innings(context, config, estimated=estimated) for context in innings)
    from apps.scoring.results import decide_match_result

    result: MatchResult | None = decide_match_result(match, results, config)
    return MatchScorecard(
        rule_version=config.version,
        match_id=match.match_id,
        innings=results,
        result=result,
        estimated=estimated,
    )
