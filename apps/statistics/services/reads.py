"""Read queries for the Phase F statistics API (Group 7).

Every endpoint reads the **digest-validated aggregate tables** (never raw
matches) plus the per-match source rows only for match-scoped statistics. The
functions return plain dicts ready for the API layer; the six-layer rule is
honoured at the view layer (views → services), so no selector module is
needed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from apps.core.enums import StatScope
from apps.scoring.types import BattingLine, BowlingLine
from apps.statistics.models import (
    FormTrend,
    HeadToHead,
    Leaderboard,
    PlayerCareerStat,
    PlayerMatchStat,
    TeamStat,
    TournamentStandings,
)
from apps.statistics.services.recompute import _dec, _lines_from

#: StatScope values the player-stats endpoint accepts.
PLAYER_SCOPES = frozenset({s.value for s in StatScope if s.value != StatScope.TEAM.value})


def _batting(official: PlayerCareerStat) -> dict[str, Any]:
    return {
        "matches": official.matches,
        "innings": official.innings,
        "runs": official.runs,
        "not_outs": official.not_outs,
        "high_score": official.high_score,
        "high_score_not_out": official.high_score_not_out,
        "average": _dec(official.batting_avg, 2),
        "strike_rate": _dec(official.strike_rate, 2),
        "hundreds": official.hundreds,
        "fifties": official.fifties,
        "fours": official.fours,
        "sixes": official.sixes,
    }


def _bowling(official: PlayerCareerStat) -> dict[str, Any]:
    return {
        "balls": official.balls_bowled,
        "runs_conceded": official.runs_conceded,
        "wickets": official.wickets,
        "maidens": official.maidens,
        "average": _dec(official.bowling_avg, 2),
        "economy": _dec(official.economy, 2),
        "strike_rate": _dec(official.bowling_sr, 2),
        "five_wicket_hauls": official.five_wicket_hauls,
        "best": {"wickets": official.best_wickets, "runs": official.best_runs},
    }


def _fielding(official: PlayerCareerStat) -> dict[str, Any]:
    return {"catches": official.catches, "stumpings": official.stumpings}


def player_statistics(
    player_id: Any,
    *,
    scope: str = StatScope.CAREER.value,
    format_type: str | None = None,
    gender: str = "M",
) -> dict[str, Any]:
    """One player's aggregate row for a scope/format (API_SPEC §4.3 shape)."""
    from apps.core.errors import NotFound
    from apps.players.models import Player

    scope = (scope or StatScope.CAREER.value).upper()
    if scope not in PLAYER_SCOPES:
        raise ValueError(f"unsupported scope: {scope!r}")
    try:
        player = Player.objects.get(pk=player_id)
    except Player.DoesNotExist as exc:
        raise NotFound(f"Player {player_id} not found") from exc
    row = (
        PlayerCareerStat.objects.filter(
            player_id=player_id, scope_type=scope, format_type=format_type, gender=gender
        )
        .select_related("player")
        .first()
    )
    if row is None:
        empty = {
            "matches": 0,
            "innings": 0,
            "runs": 0,
            "not_outs": 0,
            "high_score": None,
            "high_score_not_out": False,
            "average": None,
            "strike_rate": None,
            "hundreds": 0,
            "fifties": 0,
            "fours": 0,
            "sixes": 0,
        }
        return {
            "player": {
                "id": str(player.pk),
                "name": player.full_name,
                "role": player.primary_role,
                "batting_style": player.batting_style,
                "bowling_style": player.bowling_style,
            },
            "scope": scope,
            "format": format_type,
            "official": {
                "batting": {**empty, "high_score": None},
                "bowling": {
                    "balls": 0,
                    "runs_conceded": 0,
                    "wickets": 0,
                    "maidens": 0,
                    "average": None,
                    "economy": None,
                    "strike_rate": None,
                    "five_wicket_hauls": 0,
                    "best": {"wickets": None, "runs": None},
                },
                "fielding": {"catches": 0, "stumpings": 0},
            },
            "derived": {"label": "derived"},
            "source_kind": "computed",
        }

    return {
        "player": {
            "id": str(row.player_id),
            "name": row.player.full_name,
            "role": row.player.primary_role,
            "batting_style": row.player.batting_style,
            "bowling_style": row.player.bowling_style,
        },
        "scope": scope,
        "format": format_type,
        "official": {
            "batting": _batting(row),
            "bowling": _bowling(row),
            "fielding": _fielding(row),
        },
        "derived": {"label": "derived"},
        "source_kind": row.source_kind,
    }


def player_form(player_id: Any) -> dict[str, Any]:
    """One player's rolling form windows, newest format first."""
    rows = list(
        FormTrend.objects.filter(player_id=player_id).order_by(
            "format_type", "window_size", "metric"
        )
    )
    windows: list[dict[str, Any]] = []
    for row in rows:
        windows.append(
            {
                "format": row.format_type,
                "window": row.window_size,
                "metric": row.metric,
                "value": _dec(row.value, 3),
                "series": row.series or [],
            }
        )
    return {"player_id": str(player_id), "windows": windows}


def team_statistics(
    team_id: Any,
    *,
    scope: str = StatScope.CAREER.value,
    format_type: str | None = None,
) -> dict[str, Any]:
    """One team's aggregate row for a scope/format."""
    from apps.core.errors import NotFound
    from apps.teams.models import Team

    try:
        team = Team.objects.get(pk=team_id)
    except Team.DoesNotExist as exc:
        raise NotFound(f"Team {team_id} not found") from exc
    row = (
        TeamStat.objects.filter(team_id=team_id, scope_type=scope, format_type=format_type)
        .select_related("team")
        .first()
    )
    if row is None:
        return {
            "team": {"id": str(team.pk), "name": team.name},
            "scope": scope,
            "format": format_type,
            "official": {
                "matches": 0,
                "won": 0,
                "lost": 0,
                "tied": 0,
                "drawn": 0,
                "no_result": 0,
                "runs_scored": 0,
                "runs_conceded": 0,
                "wickets_taken": 0,
                "nrr": None,
            },
            "source_kind": "computed",
        }
    return {
        "team": {"id": str(row.team_id), "name": row.team.name},
        "scope": scope,
        "format": format_type,
        "official": {
            "matches": row.matches,
            "won": row.won,
            "lost": row.lost,
            "tied": row.tied,
            "drawn": row.drawn,
            "no_result": row.no_result,
            "runs_scored": row.runs_scored,
            "runs_conceded": row.runs_conceded,
            "wickets_taken": row.wickets_taken,
            "nrr": _dec(row.nrr, 3),
        },
        "source_kind": row.source_kind,
    }


def match_statistics(match_id: Any) -> dict[str, Any]:
    """Per-player lines for one completed match (match-level leaderboards)."""
    rows = (
        PlayerMatchStat.objects.filter(match_id=match_id)
        .select_related("player", "team")
        .order_by("player__full_name")
    )
    players: list[dict[str, Any]] = []
    for row in rows:
        batting = _lines_from(row.batting, BattingLine)
        bowling = _lines_from(row.bowling, BowlingLine)
        players.append(
            {
                "player": {
                    "id": str(row.player_id),
                    "name": row.player.full_name,
                    "team": row.team.name if row.team_id and row.team else None,
                },
                "batting": {
                    "runs": sum(b.runs for b in batting),
                    "balls": sum(b.balls for b in batting),
                    "fours": sum(b.fours for b in batting),
                    "sixes": sum(b.sixes for b in batting),
                    "out": any(b.out for b in batting),
                },
                "bowling": {
                    "wickets": sum(b.wickets for b in bowling),
                    "balls": sum(b.legal_balls for b in bowling),
                    "runs_conceded": sum(b.runs_conceded for b in bowling),
                    "maidens": sum(b.maidens for b in bowling),
                },
            }
        )
    return {"match_id": str(match_id), "players": players}


def series_points_table(series_id: Any, *, stage_id: Any = None) -> dict[str, Any]:
    """The points table for a series/stage, ordered by rank."""
    rows = list(
        TournamentStandings.objects.filter(series_id=series_id, stage_id=stage_id)
        .select_related("team")
        .order_by("rank", "team__name")
    )
    standings = [
        {
            "rank": r.rank,
            "team": {"id": str(r.team_id), "name": r.team.name},
            "played": r.matches_played,
            "won": r.matches_won,
            "lost": r.matches_lost,
            "tied": r.matches_tied,
            "drawn": r.matches_drawn,
            "no_result": r.no_result,
            "points": r.points,
            "nrr": _dec(r.nrr, 3),
            "form": r.form,
            "qualify_status": r.qualify_status,
        }
        for r in rows
    ]
    return {
        "series_id": str(series_id),
        "stage_id": str(stage_id) if stage_id else None,
        "standings": standings,
    }


def series_leaderboard(
    series_id: Any, *, format_type: str | None = None, metric: str | None = None
) -> list[dict[str, Any]]:
    """SERIES-scope leaderboard rows for a series."""
    return leaderboard_rows(
        scope=StatScope.SERIES.value,
        scope_ref_id=series_id,
        format_type=format_type,
        metric=metric,
    )


def leaderboard_rows(
    *,
    scope: str = StatScope.CAREER.value,
    scope_ref_id: Any = None,
    format_type: str | None = None,
    metric: str | None = None,
) -> list[dict[str, Any]]:
    """Precomputed leaderboard rows (ranked) for a scope/metric/format."""
    qs = Leaderboard.objects.filter(scope_type=scope, scope_ref_id=scope_ref_id)
    if format_type:
        qs = qs.filter(format_type=format_type)
    if metric:
        qs = qs.filter(metric=metric)
    rows = list(qs.select_related("player").order_by("rank", "player_id"))
    return [
        {
            "rank": r.rank,
            "player": {"id": str(r.player_id), "name": r.player.full_name if r.player else ""},
            "metric": r.metric,
            "value": _dec(r.value, 3),
            "supporting": r.supporting or {},
        }
        for r in rows
    ]


def head_to_head(team1_id: Any, team2_id: Any, *, format_type: str | None = None) -> dict[str, Any]:
    """The H2H row between two teams (canonical low/high ordering)."""
    from apps.core.errors import NotFound
    from apps.teams.models import Team

    low, high = sorted((team1_id, team2_id), key=str)
    row = (
        HeadToHead.objects.filter(team_low_id=low, team_high_id=high, format_type=format_type)
        .select_related("team_low", "team_high")
        .first()
    )
    if row is None:
        try:
            name1 = Team.objects.get(pk=team1_id).name
            name2 = Team.objects.get(pk=team2_id).name
        except Team.DoesNotExist as exc:
            raise NotFound("Unknown team id in head-to-head") from exc
        return {
            "team1": {"id": str(team1_id), "name": name1},
            "team2": {"id": str(team2_id), "name": name2},
            "format": format_type,
            "matches": 0,
            "team1_wins": 0,
            "team2_wins": 0,
            "ties": 0,
            "draws": 0,
            "no_results": 0,
            "source_kind": "computed",
        }
    # The canonical ordering stores low/high; map back to the requested teams.
    if str(row.team_low_id) == str(team1_id):
        team1_wins, team2_wins = row.low_wins, row.high_wins
    else:
        team1_wins, team2_wins = row.high_wins, row.low_wins
    return {
        "team1": {
            "id": str(team1_id),
            "name": row.team_low.name
            if str(row.team_low_id) == str(team1_id)
            else row.team_high.name,
        },
        "team2": {
            "id": str(team2_id),
            "name": row.team_high.name
            if str(row.team_high_id) == str(team2_id)
            else row.team_low.name,
        },
        "format": format_type,
        "matches": row.matches,
        "team1_wins": team1_wins,
        "team2_wins": team2_wins,
        "ties": row.ties,
        "draws": row.draws,
        "no_results": row.no_results,
        "source_kind": row.source_kind,
    }


def team_records(fmt: str | None = None) -> dict[str, Any]:
    from apps.statistics.services import records

    return records.team_records(fmt)


def player_records(fmt: str | None = None) -> dict[str, Any]:
    from apps.statistics.services import records

    return records.player_records(fmt)


def compare_players(
    ids: Sequence[str], *, format_type: str | None = None, metric: str | None = None
) -> list[dict[str, Any]]:
    from apps.statistics.services import compare

    return compare.compare_players(ids, format_type=format_type, metric=metric)


__all__ = [
    "compare_players",
    "head_to_head",
    "leaderboard_rows",
    "match_statistics",
    "player_form",
    "player_records",
    "player_statistics",
    "series_leaderboard",
    "series_points_table",
    "team_records",
    "team_statistics",
]
