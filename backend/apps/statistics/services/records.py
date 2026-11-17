"""Records catalogue (Phase F Group 4) — computed on demand, no table.

Records read the digest-validated aggregate tables for career/team milestones
and the per-match source rows for innings-level records (highest score, best
bowling figures, highest team total). Averages are qualification-gated so the
catalogue never crowns a one-innings wonder.

The catalogue is deliberately small and stable; each entry carries a
human-readable ``detail`` for the API.
"""

from __future__ import annotations

from typing import Any

from apps.core.enums import StatScope
from apps.scoring.types import BattingLine, BowlingLine
from apps.statistics.models import PlayerCareerStat, PlayerMatchStat, TeamStat
from apps.statistics.services.recompute import _lines_from

#: Minimum career innings for an average record; minimum balls for economy.
MIN_INNINGS_FOR_AVG = 20
MIN_WICKETS_FOR_BOWLING_AVG = 20
MIN_BALLS_FOR_ECONOMY = 500


def _fmt_filter(qs: Any, fmt: str | None) -> Any:
    return qs.filter(format_type=fmt) if fmt else qs


def _player_record(stat: Any, value: Any, detail: str) -> dict[str, Any]:
    return {
        "player_id": str(stat.player_id),
        "player_name": stat.player.full_name,
        "value": value,
        "detail": detail,
    }


def _highest_individual_score() -> dict[str, Any] | None:
    best: tuple[Any, Any, str] | None = None  # (row, runs, detail)
    qs = PlayerMatchStat.objects.select_related("player", "match").iterator()
    for row in qs:
        for line in _lines_from(row.batting, BattingLine):
            if best is None or line.runs > best[1]:
                suffix = "" if line.out else "*"
                best = (row, line.runs, f"{line.runs}{suffix} runs")
    if best is None:
        return None
    row, runs, detail = best
    return {
        "player_id": str(row.player_id),
        "player_name": row.player.full_name,
        "value": runs,
        "detail": detail,
    }


def _best_bowling_figures() -> dict[str, Any] | None:
    best: tuple[Any, int, int] | None = None  # (row, wickets, runs_conceded)
    for row in PlayerMatchStat.objects.select_related("player", "match").iterator():
        for line in _lines_from(row.bowling, BowlingLine):
            if line.wickets and (
                best is None or (line.wickets, -line.runs_conceded) > (best[1], -best[2])
            ):
                best = (row, line.wickets, line.runs_conceded)
    if best is None:
        return None
    row, wickets, conceded = best
    return {
        "player_id": str(row.player_id),
        "player_name": row.player.full_name,
        "value": wickets,
        "detail": f"{wickets}/{conceded}",
    }


def player_records(fmt: str | None = None) -> dict[str, dict[str, Any]]:
    """Career + innings-level player records, optional ``format`` filter."""
    qs = list(_fmt_filter(PlayerCareerStat.objects.filter(scope_type=StatScope.CAREER.value), fmt))
    records: dict[str, dict[str, Any]] = {}

    batters = [s for s in qs if s.runs]
    if batters:
        top = max(batters, key=lambda s: s.runs)
        records["most_runs"] = _player_record(
            top, top.runs, f"{top.runs} runs in {top.matches} matches"
        )
        qualified = [s for s in batters if s.innings >= MIN_INNINGS_FOR_AVG and s.batting_avg]
        if qualified:
            best = max(qualified, key=lambda s: s.batting_avg)
            records["best_batting_avg"] = _player_record(
                best, float(best.batting_avg), f"{best.batting_avg:.2f} average"
            )
        for metric, field, label in (
            ("most_hundreds", "hundreds", "hundreds"),
            ("most_fifties", "fifties", "fifties"),
            ("most_fours", "fours", "fours"),
            ("most_sixes", "sixes", "sixes"),
        ):
            eligible = [s for s in qs if getattr(s, field)]
            if eligible:
                holder = max(eligible, key=lambda s: getattr(s, field))
                records[metric] = _player_record(
                    holder, getattr(holder, field), f"{getattr(holder, field)} {label}"
                )

    bowlers = [s for s in qs if s.wickets]
    if bowlers:
        top = max(bowlers, key=lambda s: s.wickets)
        records["most_wickets"] = _player_record(
            top, top.wickets, f"{top.wickets} wickets in {top.matches} matches"
        )
        qualified = [
            s for s in bowlers if s.wickets >= MIN_WICKETS_FOR_BOWLING_AVG and s.bowling_avg
        ]
        if qualified:
            best = min(qualified, key=lambda s: s.bowling_avg)
            records["best_bowling_avg"] = _player_record(
                best, float(best.bowling_avg), f"{best.bowling_avg:.2f} average"
            )
        economical = [s for s in bowlers if s.balls_bowled >= MIN_BALLS_FOR_ECONOMY and s.economy]
        if economical:
            best = min(economical, key=lambda s: s.economy)
            records["best_economy"] = _player_record(
                best, float(best.economy), f"{best.economy:.2f} economy"
            )
        five_for = [s for s in qs if s.five_wicket_hauls]
        if five_for:
            holder = max(five_for, key=lambda s: s.five_wicket_hauls)
            records["most_five_wicket_hauls"] = _player_record(
                holder,
                holder.five_wicket_hauls,
                f"{holder.five_wicket_hauls} five-wicket hauls",
            )

    highest = _highest_individual_score()
    if highest is not None:
        records["highest_individual_score"] = highest
    figures = _best_bowling_figures()
    if figures is not None:
        records["best_bowling_figures"] = figures
    return records


def _team_record(stat: Any, value: Any, detail: str) -> dict[str, Any]:
    return {
        "team_id": str(stat.team_id),
        "team_name": stat.team.name,
        "value": value,
        "detail": detail,
    }


def team_records(fmt: str | None = None) -> dict[str, dict[str, Any]]:
    """Team records: milestones from aggregates + the highest innings total."""
    qs = list(_fmt_filter(TeamStat.objects.filter(scope_type=StatScope.CAREER.value), fmt))
    records: dict[str, dict[str, Any]] = {}

    for metric, field, label in (
        ("most_wins", "won", "wins"),
        ("most_runs_scored", "runs_scored", "runs"),
        ("most_wickets_taken", "wickets_taken", "wickets"),
    ):
        eligible = [s for s in qs if getattr(s, field)]
        if eligible:
            holder = max(eligible, key=lambda s: getattr(s, field))
            records[metric] = _team_record(
                holder, getattr(holder, field), f"{getattr(holder, field)} {label}"
            )

    from apps.innings.models import InningsSummary

    summary = InningsSummary.objects.order_by("-runs").select_related("innings").first()
    if summary is not None:
        innings = summary.innings
        records["highest_team_total"] = {
            "team_id": str(innings.batting_team_id),
            "team_name": innings.batting_team.name,
            "value": summary.runs,
            "detail": f"{summary.runs} runs",
        }
    return records


__all__ = ["player_records", "team_records"]
