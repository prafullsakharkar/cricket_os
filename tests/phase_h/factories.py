"""Shared Phase H pipeline/command/merge test factories.

Builds a completed match (Match + teams + result), per-player stats with
batting/bowling JSON lines in the shape the snapshot builder consumes, and a
``FantasyRulesetVersion`` whose point rules round-trip through the engine
``point_rule`` factory into the DB condition JSON.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.fantasy.engine import PointRuleSpec, point_rule
from apps.fantasy.models import FantasyPointRule, FantasyRuleset, FantasyRulesetVersion
from apps.matches.models import Match, MatchPlayer, MatchResult
from apps.statistics.models import PlayerMatchStat

from ..phase_c.factories import make_match, make_match_teams, make_player, make_team

DEFAULT_RULES: tuple[PointRuleSpec, ...] = (point_rule("runs", "per_unit", "1.00", "batting"),)


def _condition(rule: PointRuleSpec) -> dict[str, Any]:
    """The engine rule's params as the DB ``condition`` JSON."""
    params: dict[str, Any] = {"operator": rule.operator}
    if rule.unit is not None:
        params["unit"] = rule.unit
    if rule.min_value is not None:
        params["min_value"] = rule.min_value
    if rule.max_value is not None:
        params["max_value"] = rule.max_value
    if rule.tiers:
        params["tiers"] = [(t.min_value, str(t.points)) for t in rule.tiers]
    if rule.tier_mode is not None:
        params["tier_mode"] = rule.tier_mode
    if rule.min_balls is not None:
        params["min_balls"] = rule.min_balls
    if rule.min_overs is not None:
        params["min_overs"] = rule.min_overs
    if rule.cap_value is not None:
        params["cap_value"] = str(rule.cap_value)
    return params


def make_ruleset_version(
    *,
    version_number: int = 1,
    auto_recompute: bool = True,
    rules: tuple[PointRuleSpec, ...] | None = None,
    policies: dict[str, Any] | None = None,
) -> tuple[FantasyRuleset, FantasyRulesetVersion]:
    """Create a ruleset with one version and its point rules."""
    ruleset = FantasyRuleset.objects.create(name="test ruleset", provider_scope="generic")
    version = FantasyRulesetVersion.objects.create(
        ruleset=ruleset,
        version=version_number,
        effective_from=timezone.now(),
        auto_recompute=auto_recompute,
        policies=policies or {},
    )
    for rule in rules if rules is not None else DEFAULT_RULES:
        FantasyPointRule.objects.create(
            ruleset_version=version,
            event_type=rule.metric,
            role_scope=rule.scope,
            points=rule.points,
            condition=_condition(rule),
        )
    return ruleset, version


def make_completed_match(*, format_type: str = "T20I", **overrides: Any) -> tuple[Match, Any, Any]:
    match = make_match(
        format_type=format_type,
        status=MatchStatus.COMPLETED,
        end_time_utc=timezone.now(),
        **overrides,
    )
    team1 = make_team("India")
    team2 = make_team("Australia")
    make_match_teams(match, team1, team2)
    MatchResult.objects.create(match=match, result_type="WIN", winner_team=team1)
    return match, team1, team2


def _bat(
    runs: int,
    balls: int = 0,
    out: bool = False,
    dismissal_type: str | None = None,
    fielder1_id: str | None = None,
    bowler_id: str | None = None,
) -> dict[str, Any]:
    return {
        "rule_version": "test",
        "player_id": "p",
        "did_bat": True,
        "runs": runs,
        "balls": balls,
        "fours": 0,
        "sixes": 0,
        "ones": 0,
        "twos": 0,
        "threes": 0,
        "fives": 0,
        "dots": 0,
        "out": out,
        "dismissal_type": dismissal_type,
        "bowler_id": bowler_id,
        "fielder1_id": fielder1_id,
        "fielder2_id": None,
        "fielder3_id": None,
        "retirement_type": None,
        "strike_rate": None,
    }


def _bowl(
    legal_balls: int,
    runs_conceded: int,
    wickets: int = 0,
    maidens: int = 0,
    dots: int = 0,
) -> dict[str, Any]:
    return {
        "rule_version": "test",
        "player_id": "p",
        "legal_balls": legal_balls,
        "runs_conceded": runs_conceded,
        "wickets": wickets,
        "maidens": maidens,
        "wides": 0,
        "noballs": 0,
        "dots": dots,
        "overs_display": f"{legal_balls // 6}.{legal_balls % 6}",
        "economy": None,
    }


def add_stat(
    match: Match,
    team: Any,
    *,
    runs: int = 0,
    balls: int = 0,
    out: bool = False,
    dismissal_type: str | None = None,
    wickets: int = 0,
    balls_bowled: int = 0,
    runs_conceded: int = 0,
    is_partial: bool = False,
) -> tuple[Any, PlayerMatchStat]:
    """Add one player with batting/bowling lines to a match."""
    player = make_player()
    MatchPlayer.objects.create(match=match, player=player, team=team, is_playing_xi=True)
    batting = (
        [_bat(runs=runs, balls=balls, out=out, dismissal_type=dismissal_type)]
        if (runs or balls or out)
        else []
    )
    bowling = (
        [_bowl(legal_balls=balls_bowled, runs_conceded=runs_conceded, wickets=wickets)]
        if (balls_bowled or wickets or runs_conceded)
        else []
    )
    stat = PlayerMatchStat.objects.create(
        match=match,
        player=player,
        team=team,
        did_bat=bool(batting),
        did_bowl=bool(bowling),
        did_field=True,
        batting=batting,
        bowling=bowling,
        fielding={},
        is_partial=is_partial,
    )
    return player, stat
