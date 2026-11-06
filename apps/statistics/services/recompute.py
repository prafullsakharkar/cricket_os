"""Tier-2 recompute pipeline + sweep (Phase F Group 2).

The pipeline turns the per-match source rows (``player_match_stat``,
``innings_summary``, ``match_result``) into the §3.7 aggregate tables. Design
(ledger decisions 3, 9, 10):

* **Recompute-from-source, never delta.** Every aggregate key is recomputed
  from the durable source rows it depends on, so a correction or a backfill
  reproduces identical results and the pipeline is order-independent.
* **Keyed recompute + advisory-lock serialization.** Each aggregate key runs
  in its own transaction under ``pg_advisory_xact_lock``, so concurrent
  recomputes of the same key serialise (the match-event-log precedent).
* **Idempotence by digest.** A row whose ``inputs_digest`` (canonical source
  rows + rule version) is unchanged is left untouched — ``revision`` and
  ``computed_at`` move only when the source actually changed.
* **Watermark + sweep.** Every terminal match records the digest of its
  source and the set of keys it contributed to (``statistics_watermark``). A
  correction recomputes the union of the previous and current key sets;
  ``sweep_stale_statistics`` heals matches whose source changed behind the
  pipeline's back or whose recompute crashed mid-flight.
* **Merge integration.** The aggregate FKs are NOT registered with the merge
  registry — their expression uniques would be violated by a re-point (ledger
  decision 9, refined at implementation). ``player_match_stat`` is not
  registered either: the naive re-point would violate ``ux_player_match_stat``
  when both identities played the same match. The ``player.merged`` subscriber
  (which runs synchronously inside the merge transaction) re-points the source
  rows itself — merging same-match rows — deletes the loser's aggregate rows,
  and recomputes the winner's scopes from source.

``stats.updated`` / ``standings.updated`` are published after recompute for
cache invalidation (ledger decision 5). Leaderboard/form keys are added by
Group 4; the key scheme leaves the ``leaderboard:`` / ``form:`` prefixes
reserved.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.enums import MatchStatus, StatScope
from apps.matches.models import Match, MatchResult
from apps.scoring.aggregates import career_totals, season_totals
from apps.scoring.config import RuleConfig, preset_for_format
from apps.scoring.standings import build_standings
from apps.scoring.types import (
    BattingLine,
    BowlingLine,
    Format,
    PlayerMatchLine,
    ResultType,
    TeamMatchLine,
)
from apps.statistics.models import (
    FormTrend,
    HeadToHead,
    Leaderboard,
    PlayerCareerStat,
    PlayerMatchStat,
    SourceKind,
    StatisticsWatermark,
    TeamStat,
    TournamentStandings,
)

#: Result types that count as a completed contest (not a no-result/void match).
_WIN_RESULTS = {ResultType.WIN, ResultType.FORFEIT, ResultType.SUPER_OVER_TIE_BREAK}
_NO_RESULTS = {ResultType.NO_RESULT, ResultType.ABANDONED, ResultType.CANCELLED}


# --------------------------------------------------------------------------- #
# Key scheme
# --------------------------------------------------------------------------- #
# career:{player}:{scope}:{ref|'-'}:{fmt|'-'}:{gender}
# team:{team}:{scope}:{ref|'-'}:{fmt|'-'}
# h2h:{low}:{high}:{fmt|'-'}
# standings:{series}:{stage|'-'}
# leaderboard:{player|team}:{scope}:{ref|'-'}:{fmt|'-'}
# form:{player}:{fmt|'-'}


def _career_key(player_id: Any, scope: StatScope, ref: Any, fmt: str | None, gender: str) -> str:
    return f"career:{player_id}:{scope.value}:{ref or '-'}:{fmt or '-'}:{gender}"


def _team_key(team_id: Any, scope: StatScope, ref: Any, fmt: str | None) -> str:
    return f"team:{team_id}:{scope.value}:{ref or '-'}:{fmt or '-'}"


def dependency_keys_for_match(match: Match) -> set[str]:
    """Every aggregate key a match contributes to (its dependency set)."""
    keys: set[str] = set()
    gender = match.gender or "M"
    fmt = match.format_type
    stat_rows = list(match.player_stats.all())
    team_ids = sorted(str(t) for t in match.teams.values_list("team_id", flat=True))

    for row in stat_rows:
        if not row.team_id:
            continue
        pid = str(row.player_id)
        player_team = str(row.team_id)
        keys.add(_career_key(pid, StatScope.CAREER, None, None, gender))
        keys.add(_career_key(pid, StatScope.FORMAT, None, fmt, gender))
        if match.season_id:
            keys.add(_career_key(pid, StatScope.SEASON, match.season_id, None, gender))
        if match.series_id:
            keys.add(_career_key(pid, StatScope.SERIES, match.series_id, None, gender))
        keys.add(_career_key(pid, StatScope.TEAM, player_team, None, gender))
        for tid in team_ids:
            if tid != player_team:
                keys.add(_career_key(pid, StatScope.OPPOSITION, tid, None, gender))
        if match.venue_id:
            keys.add(_career_key(pid, StatScope.VENUE, match.venue_id, None, gender))

    for tid in team_ids:
        keys.add(_team_key(tid, StatScope.CAREER, None, None))
        keys.add(_team_key(tid, StatScope.FORMAT, None, fmt))
        if match.season_id:
            keys.add(_team_key(tid, StatScope.SEASON, match.season_id, None))
        if match.series_id:
            keys.add(_team_key(tid, StatScope.SERIES, match.series_id, None))
            if getattr(match.series, "is_tournament", False):
                keys.add(_team_key(tid, StatScope.TOURNAMENT, match.series_id, None))
        if match.venue_id:
            keys.add(_team_key(tid, StatScope.VENUE, match.venue_id, None))

    if len(team_ids) >= 2:
        low, high = team_ids[0], team_ids[1]
        keys.add(f"h2h:{low}:{high}:{fmt}")
        keys.add(f"h2h:{low}:{high}:-")
    if stat_rows:
        keys.add("leaderboard:player:CAREER:-:-")
        keys.add(f"leaderboard:player:FORMAT:-:{fmt}")
        for row in stat_rows:
            keys.add(f"form:{row.player_id}:{fmt}")
            keys.add(f"form:{row.player_id}:-")
    if match.series_id:
        keys.add(f"leaderboard:player:SERIES:{match.series_id}:-")
        keys.add(f"leaderboard:player:SERIES:{match.series_id}:{fmt}")
        keys.add(f"standings:{match.series_id}:{match.stage_id or '-'}")
    return keys


# --------------------------------------------------------------------------- #
# Digest + serialisation helpers
# --------------------------------------------------------------------------- #


def _digest(rows: list[dict[str, Any]], rule_version: str = "") -> str:
    """Deterministic SHA-256 over canonical source rows + rule version."""
    payload = {"rule_version": rule_version, "rows": rows}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dec(value: float | Decimal | None, places: int) -> Decimal | None:
    if value is None:
        return None
    return Decimal(f"{float(value):.{places}f}")


def _stat_source(row: PlayerMatchStat) -> dict[str, Any]:
    return {
        "match_id": str(row.match_id),
        "match_date": str(row.match.source_local_date),
        "team_id": str(row.team_id) if row.team_id else None,
        "is_partial": row.is_partial,
        "did_bat": row.did_bat,
        "did_bowl": row.did_bowl,
        "batting": row.batting,
        "bowling": row.bowling,
    }


def _coverage(rows: Iterable[Any], dates: Iterable[Any]) -> dict[str, Any]:
    date_list = [d for d in dates if d]
    return {
        "matches_counted": len({str(r.match_id) for r in rows}),
        "partial_matches": sum(1 for r in rows if r.is_partial),
        "first_match_date": min(date_list) if date_list else None,
        "last_match_date": max(date_list) if date_list else None,
        "is_partial": any(getattr(r, "is_partial", False) for r in rows),
    }


def _coverage_matches(matches: list[Match]) -> dict[str, Any]:
    dates = [m.source_local_date for m in matches if m.source_local_date]
    return {
        "matches_counted": len(matches),
        "partial_matches": 0,
        "first_match_date": min(dates) if dates else None,
        "last_match_date": max(dates) if dates else None,
        "is_partial": False,
    }


def _lines_from(payload: Any, cls: type[Any]) -> tuple[Any, ...]:
    """Rebuild engine lines from stored JSON, tolerating schema drift.

    Unknown keys (from a newer/older rule version) are dropped; non-list
    payloads (an empty ``{}`` default) yield no lines.
    """
    if not isinstance(payload, list):
        return ()
    fields = set(getattr(cls, "__dataclass_fields__", ()))
    return tuple(
        cls(**{k: v for k, v in d.items() if k in fields}) for d in payload if isinstance(d, dict)
    )


def _player_line(row: PlayerMatchStat, config: RuleConfig) -> PlayerMatchLine:
    return PlayerMatchLine(
        rule_version=config.version,
        match_id=str(row.match_id),
        player_id=str(row.player_id),
        team_id=str(row.team_id) if row.team_id else "",
        format=Format(row.match.format_type),
        did_bat=row.did_bat,
        did_bowl=row.did_bowl,
        batting=_lines_from(row.batting, BattingLine),
        bowling=_lines_from(row.bowling, BowlingLine),
        is_super_over=False,
    )


def _match_source_digest(match: Match) -> str:
    """The digest of everything a match's aggregates depend on (watermark)."""
    result = MatchResult.objects.filter(match_id=match.id).first()
    innings = list(
        match.innings.filter(summary__isnull=False)
        .select_related("summary")
        .order_by("innings_number")
    )
    stats = list(match.player_stats.all().order_by("player_id"))
    payload = {
        "match_id": str(match.id),
        "format_type": match.format_type,
        "gender": match.gender,
        "date": str(match.source_local_date),
        "venue_id": str(match.venue_id) if match.venue_id else None,
        "series_id": str(match.series_id) if match.series_id else None,
        "stage_id": str(match.stage_id) if match.stage_id else None,
        "season_id": str(match.season_id) if match.season_id else None,
        "result": (
            {
                "result_type": result.result_type,
                "winner_team_id": (str(result.winner_team_id) if result.winner_team_id else None),
            }
            if result
            else None
        ),
        "innings": [
            {
                "number": i.innings_number,
                "batting_team_id": str(i.batting_team_id),
                "bowling_team_id": str(i.bowling_team_id),
                "all_out": i.all_out,
                "runs": i.summary.runs,
                "wickets": i.summary.wickets,
                "legal_balls": i.summary.legal_balls,
            }
            for i in innings
        ],
        "player_stats": [_stat_source(r) for r in stats],
    }
    return _digest([payload])


# --------------------------------------------------------------------------- #
# Per-key recompute (from source)
# --------------------------------------------------------------------------- #


def _recompute_career_key(parts: list[str]) -> None:
    player_id = UUID(parts[1])
    scope = parts[2]
    ref = None if parts[3] == "-" else UUID(parts[3])
    fmt = None if parts[4] == "-" else parts[4]
    gender = parts[5]

    qs = PlayerMatchStat.objects.filter(player_id=player_id).select_related("match")
    if scope == StatScope.FORMAT.value:
        assert fmt is not None
        qs = qs.filter(match__format_type=fmt)
    elif scope == StatScope.SEASON.value and ref is not None:
        qs = qs.filter(match__season_id=ref)
    elif scope == StatScope.SERIES.value and ref is not None:
        qs = qs.filter(match__series_id=ref)
    elif scope == StatScope.TEAM.value and ref is not None:
        qs = qs.filter(team_id=ref)
    elif scope == StatScope.VENUE.value and ref is not None:
        qs = qs.filter(match__venue_id=ref)
    elif scope == StatScope.OPPOSITION.value and ref is not None:
        qs = qs.exclude(team_id=ref).filter(match__teams__team_id=ref).distinct()
    rows = list(qs.order_by("match_id"))

    lookup: dict[str, Any] = {
        "player_id": player_id,
        "scope_type": scope,
        "scope_ref_id": ref,
        "format_type": fmt,
        "gender": gender,
    }
    if not rows:
        PlayerCareerStat.objects.filter(**lookup).delete()
        return

    config = preset_for_format(Format(rows[0].match.format_type))
    totals = career_totals(str(player_id), [_player_line(r, config) for r in rows], config)
    digest = _digest([_stat_source(r) for r in rows], config.version)
    coverage = _coverage(rows, [r.match.source_local_date for r in rows])

    defaults: dict[str, Any] = {
        "scope_type": scope,
        "scope_ref_id": ref,
        "format_type": fmt,
        "gender": gender,
        "matches": totals.matches,
        "innings": totals.innings,
        "not_outs": totals.not_outs,
        "runs": totals.runs,
        "balls_faced": totals.balls_faced,
        "high_score": totals.high_score,
        "high_score_not_out": totals.high_score_not_out,
        "fifties": totals.fifties,
        "hundreds": totals.hundreds,
        "fours": totals.fours,
        "sixes": totals.sixes,
        "batting_avg": _dec(totals.batting_avg, 2),
        "strike_rate": _dec(totals.strike_rate, 2),
        "catches": 0,
        "stumpings": 0,
        "balls_bowled": totals.balls_bowled,
        "runs_conceded": totals.runs_conceded,
        "wickets": totals.wickets,
        "maidens": totals.maidens,
        "best_wickets": totals.best_wickets,
        "best_runs": totals.best_runs,
        "five_wicket_hauls": totals.five_wicket_hauls,
        "bowling_avg": _dec(totals.bowling_avg, 2),
        "economy": _dec(totals.economy, 2),
        "bowling_sr": _dec(totals.bowling_sr, 2),
        "rule_version": config.version,
        "inputs_digest": digest,
        "source_kind": SourceKind.COMPUTED,
        "matches_counted": coverage["matches_counted"],
        "partial_matches": coverage["partial_matches"],
        "first_match_date": coverage["first_match_date"],
        "last_match_date": coverage["last_match_date"],
        "is_partial": coverage["is_partial"],
    }
    existing = PlayerCareerStat.objects.filter(**lookup).first()
    if existing is not None and existing.inputs_digest == digest:
        return
    defaults["revision"] = (existing.revision + 1) if existing else 1
    defaults["computed_at"] = timezone.now()
    PlayerCareerStat.objects.update_or_create(**lookup, defaults=defaults)


def _team_matches(team_id: UUID, scope: str, ref: UUID | None, fmt: str | None) -> list[Match]:
    qs = Match.objects.filter(status=MatchStatus.COMPLETED, teams__team_id=team_id)
    if scope == StatScope.FORMAT.value:
        assert fmt is not None
        qs = qs.filter(format_type=fmt)
    elif scope == StatScope.SEASON.value and ref is not None:
        qs = qs.filter(season_id=ref)
    elif scope == StatScope.SERIES.value and ref is not None:
        qs = qs.filter(series_id=ref)
    elif scope == StatScope.TOURNAMENT.value and ref is not None:
        qs = qs.filter(series_id=ref, series__is_tournament=True)
    elif scope == StatScope.VENUE.value and ref is not None:
        qs = qs.filter(venue_id=ref)
    return list(qs.distinct().order_by("start_time_utc", "id"))


def _team_match_source(
    match: Match, team_id: Any, config: RuleConfig
) -> tuple[TeamMatchLine, int, dict[str, Any]]:
    """One team's line, wickets taken, and canonical source row for a match."""
    innings_rows = list(
        match.innings.filter(summary__isnull=False)
        .select_related("summary")
        .order_by("innings_number")
    )
    team_id = str(team_id)
    for_batting = [i for i in innings_rows if str(i.batting_team_id) == team_id]
    against = [i for i in innings_rows if str(i.bowling_team_id) == team_id]
    result = MatchResult.objects.filter(match_id=match.id).first()
    result_type = ResultType(result.result_type) if result and result.result_type else None
    winner_team_id = str(result.winner_team_id) if result and result.winner_team_id else None

    line = TeamMatchLine(
        rule_version=config.version,
        match_id=str(match.id),
        team_id=team_id,
        format=Format(match.format_type),
        runs_for=sum(i.summary.runs for i in for_batting),
        balls_faced=sum(i.summary.legal_balls for i in for_batting),
        all_out=any(i.all_out for i in for_batting),
        overs_limit=config.overs_limit,
        runs_against=sum(i.summary.runs for i in against),
        balls_bowled=sum(i.summary.legal_balls for i in against),
        opponent_all_out=any(i.all_out for i in against),
        result_type=result_type,
        won=winner_team_id == team_id,
        is_super_over=False,
        is_no_result=result_type in _NO_RESULTS if result_type else False,
    )
    wickets_taken = sum(i.summary.wickets for i in against)
    source = {
        "match_id": str(match.id),
        "match_date": str(match.source_local_date),
        "result_type": result_type.value if result_type else None,
        "winner_team_id": winner_team_id,
        "innings": [
            {
                "number": i.innings_number,
                "batting_team_id": str(i.batting_team_id),
                "bowling_team_id": str(i.bowling_team_id),
                "all_out": i.all_out,
                "runs": i.summary.runs,
                "wickets": i.summary.wickets,
                "legal_balls": i.summary.legal_balls,
            }
            for i in innings_rows
        ],
    }
    return line, wickets_taken, source


def _recompute_team_key(parts: list[str]) -> None:
    team_id = UUID(parts[1])
    scope = parts[2]
    ref = None if parts[3] == "-" else UUID(parts[3])
    fmt = None if parts[4] == "-" else parts[4]

    lookup: dict[str, Any] = {
        "team_id": team_id,
        "scope_type": scope,
        "scope_ref_id": ref,
        "format_type": fmt,
    }
    matches = _team_matches(team_id, scope, ref, fmt)
    if not matches:
        TeamStat.objects.filter(**lookup).delete()
        return

    config = preset_for_format(Format(matches[0].format_type))
    lines: list[TeamMatchLine] = []
    wickets_taken = 0
    source: list[dict[str, Any]] = []
    for match in matches:
        line, wkts, src = _team_match_source(match, team_id, config)
        lines.append(line)
        wickets_taken += wkts
        source.append(src)
    source.sort(key=lambda s: s["match_id"])
    totals = season_totals(str(team_id), lines, config)
    digest = _digest(source, config.version)
    coverage = _coverage_matches(matches)

    defaults: dict[str, Any] = {
        "scope_type": scope,
        "scope_ref_id": ref,
        "format_type": fmt,
        "matches": totals.matches,
        "won": totals.won,
        "lost": totals.lost,
        "tied": totals.tied,
        "drawn": totals.drawn,
        "no_result": totals.no_result,
        "runs_scored": totals.runs_for,
        "runs_conceded": totals.runs_against,
        "wickets_taken": wickets_taken,
        "nrr": _dec(totals.nrr, 3),
        "rule_version": config.version,
        "inputs_digest": digest,
        "source_kind": SourceKind.COMPUTED,
        "matches_counted": coverage["matches_counted"],
        "partial_matches": 0,
        "first_match_date": coverage["first_match_date"],
        "last_match_date": coverage["last_match_date"],
        "is_partial": False,
    }
    existing = TeamStat.objects.filter(**lookup).first()
    if existing is not None and existing.inputs_digest == digest:
        return
    defaults["revision"] = (existing.revision + 1) if existing else 1
    defaults["computed_at"] = timezone.now()
    TeamStat.objects.update_or_create(**lookup, defaults=defaults)


def _recompute_h2h_key(parts: list[str]) -> None:
    low = UUID(parts[1])
    high = UUID(parts[2])
    fmt = None if parts[3] == "-" else parts[3]

    matches = list(
        Match.objects.filter(
            status=MatchStatus.COMPLETED,
            teams__team_id=low,
        )
        .filter(teams__team_id=high)
        .distinct()
        .order_by("end_time_utc", "start_time_utc", "id")
    )
    if fmt:
        matches = [m for m in matches if m.format_type == fmt]

    lookup: dict[str, Any] = {"team_low_id": low, "team_high_id": high, "format_type": fmt}
    if not matches:
        HeadToHead.objects.filter(**lookup).delete()
        return

    low_wins = high_wins = ties = draws = no_results = 0
    source: list[dict[str, Any]] = []
    for match in matches:
        result = MatchResult.objects.filter(match_id=match.id).first()
        result_type = ResultType(result.result_type) if result and result.result_type else None
        winner_team_id = str(result.winner_team_id) if result and result.winner_team_id else None
        if result_type is ResultType.TIE:
            ties += 1
        elif result_type is ResultType.DRAW:
            draws += 1
        elif result_type in _NO_RESULTS:
            no_results += 1
        elif result_type in _WIN_RESULTS:
            if winner_team_id == str(low):
                low_wins += 1
            elif winner_team_id == str(high):
                high_wins += 1
        source.append(
            {
                "match_id": str(match.id),
                "match_date": str(match.source_local_date),
                "result_type": result_type.value if result_type else None,
                "winner_team_id": winner_team_id,
            }
        )
    source.sort(key=lambda s: s["match_id"])
    config = preset_for_format(Format(matches[-1].format_type))
    digest = _digest(source, config.version)
    coverage = _coverage_matches(matches)

    defaults: dict[str, Any] = {
        "format_type": fmt,
        "matches": len(matches),
        "low_wins": low_wins,
        "high_wins": high_wins,
        "ties": ties,
        "draws": draws,
        "no_results": no_results,
        "last_match": matches[-1],
        "rule_version": config.version,
        "inputs_digest": digest,
        "source_kind": SourceKind.COMPUTED,
        "matches_counted": coverage["matches_counted"],
        "partial_matches": 0,
        "first_match_date": coverage["first_match_date"],
        "last_match_date": coverage["last_match_date"],
        "is_partial": False,
    }
    existing = HeadToHead.objects.filter(**lookup).first()
    if existing is not None and existing.inputs_digest == digest:
        return
    defaults["computed_at"] = timezone.now()
    HeadToHead.objects.update_or_create(**lookup, defaults=defaults)


def _config_for_series(config: RuleConfig, series: Any) -> RuleConfig:
    """The format config with the series' ``points_system`` overrides applied.

    ``points_system`` is a JSON object on the series (``{"points_win": 4,
    "points_no_result": 0, ...}``); unknown keys are ignored. ``dataclasses.replace``
    keeps the frozen ``RuleConfig`` contract — only the five points fields move.
    """
    if series is None or not series.points_system:
        return config
    overrides = {
        key: int(series.points_system[key])
        for key in ("points_win", "points_tie", "points_no_result", "points_draw", "points_loss")
        if key in series.points_system
    }
    return dataclasses.replace(config, **cast(dict[str, Any], overrides)) if overrides else config


def _result_char(line: TeamMatchLine) -> str:
    """One form character: W/L/T/D/N (TIE/DRAW/NO_RESULT + win/loss)."""
    if line.is_no_result:
        return "N"
    if line.result_type is ResultType.TIE:
        return "T"
    if line.result_type is ResultType.DRAW:
        return "D"
    if line.result_type in _WIN_RESULTS:
        return "W" if line.won else "L"
    return "N"


def _form_string(chars: Sequence[str]) -> str | None:
    """Most-recent-first window of 5 result characters (empty → ``None``)."""
    window = list(chars)[-5:]
    return "".join(reversed(window)) if window else None


def _recompute_standings_key(parts: list[str]) -> None:
    from apps.competitions.models import MatchStage, Series

    series_id = UUID(parts[1])
    stage_id = None if parts[2] == "-" else UUID(parts[2])

    series = Series.objects.filter(id=series_id).first()
    matches = list(
        Match.objects.filter(series_id=series_id, status=MatchStatus.COMPLETED).order_by(
            "start_time_utc", "id"
        )
    )
    if stage_id:
        matches = [m for m in matches if m.stage_id == stage_id]

    if not matches:
        TournamentStandings.objects.filter(series_id=series_id, stage_id=stage_id).delete()
        return

    base_config = preset_for_format(Format(matches[0].format_type))
    config = _config_for_series(base_config, series)

    team_ids: list[str] = []
    lines: list[TeamMatchLine] = []
    source: list[dict[str, Any]] = []
    #: team id → result chars in match (oldest→newest) order, for the form window.
    form_chars: dict[str, list[str]] = {}
    for match in matches:
        for raw_tid in match.teams.values_list("team_id", flat=True):
            tid = str(raw_tid)
            if tid not in team_ids:
                team_ids.append(tid)
            line, _wkts, src = _team_match_source(match, tid, config)
            lines.append(line)
            source.append(src)
            if line.result_type is not None:
                form_chars.setdefault(tid, []).append(_result_char(line))
    source.sort(key=lambda s: s["match_id"])
    rows = build_standings(team_ids, lines, config)
    # The effective points config is part of the inputs: changing
    # points_system must change the digest so a recompute actually triggers.
    points_config = {
        key: getattr(config, key)
        for key in ("points_win", "points_tie", "points_no_result", "points_draw", "points_loss")
    }
    digest = _digest([*source, {"points_config": points_config}], config.version)
    coverage = _coverage_matches(matches)

    stage = None
    group_name = None
    if stage_id:
        stage = MatchStage.objects.filter(id=stage_id).first()
        group_name = stage.group_name if stage else None

    no_of_qual_default = (
        int(series.points_system["no_of_qual"])
        if series is not None and series.points_system and "no_of_qual" in series.points_system
        else None
    )

    for row in rows:
        existing = TournamentStandings.objects.filter(
            series_id=series_id, stage_id=stage_id, team_id=row.team_id
        ).first()
        if existing is not None and existing.inputs_digest == digest:
            continue
        # Steward-set fields survive recomputes; only fresh rows pick defaults.
        qualify_status = existing.qualify_status if existing is not None else None
        no_of_qual = existing.no_of_qual if existing is not None else no_of_qual_default
        defaults: dict[str, Any] = {
            "stage": stage,
            "group_name": group_name,
            "rank": row.position,
            "matches_played": row.played,
            "matches_won": row.won,
            "matches_lost": row.lost,
            "matches_tied": row.tied,
            "matches_drawn": row.drawn,
            "no_result": row.no_result,
            "points": row.points,
            "nrr": _dec(row.nrr, 3),
            "form": _form_string(form_chars.get(row.team_id, ())),
            "qualify_status": qualify_status,
            "no_of_qual": no_of_qual,
            "rule_version": config.version,
            "inputs_digest": digest,
            "source_kind": SourceKind.COMPUTED,
            "matches_counted": coverage["matches_counted"],
            "partial_matches": 0,
            "first_match_date": coverage["first_match_date"],
            "last_match_date": coverage["last_match_date"],
            "is_partial": False,
        }
        defaults["revision"] = (existing.revision + 1) if existing else 1
        defaults["computed_at"] = timezone.now()
        TournamentStandings.objects.update_or_create(
            series_id=series_id,
            stage_id=stage_id,
            team_id=row.team_id,
            defaults=defaults,
        )
    TournamentStandings.objects.filter(series_id=series_id, stage_id=stage_id).exclude(
        team_id__in=team_ids
    ).delete()


def _nrr_for_diff(value: float | Decimal | None) -> float | None:
    """NRR rounded to 3 dp for reconciliation comparison (provider tolerance)."""
    if value is None:
        return None
    return round(float(value), 3)


def _diff_standings(
    ours: Mapping[str, Mapping[str, Any]],
    provider_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Per-team field diffs between pipeline and provider standings rows."""
    differences: dict[str, Any] = {}
    fields = ("played", "won", "lost", "tied", "drawn", "no_result", "points", "nrr")
    for team_id in sorted(set(ours) | set(provider_rows)):
        a, b = ours.get(team_id), provider_rows.get(team_id)
        if a is None or b is None:
            differences[team_id] = {"ours": a, "provider": b}
            continue
        for key in fields:
            left = _nrr_for_diff(a.get(key)) if key == "nrr" else a.get(key)
            right = _nrr_for_diff(b.get(key)) if key == "nrr" else b.get(key)
            if left != right:
                differences.setdefault(team_id, {})[key] = {"ours": left, "provider": right}
    return differences


def reconcile_standings(
    series_id: Any,
    provider_rows: Mapping[str, Mapping[str, Any]],
    *,
    stage_id: Any = None,
    provider: Any = None,
) -> Any:
    """Diff the pipeline's standings against the provider's points table.

    ``provider_rows`` maps team id (str) → ``{played, won, lost, tied, drawn,
    no_result, points, nrr}``. The pipeline standings for the series/stage are
    recomputed fresh, then diffed. A mismatch opens a ``ReconciliationJob``
    (``scope_key = standings:{series}:{stage|'-'}``) and writes the
    ``reconciliation_mismatch`` DLQ row — never a silent correction (the
    reconciliation invariant). Returns the job, or ``None`` when they agree.
    """
    from apps.synchronization.services.reconciliation import open_job, report_mismatch

    stage_key = stage_id if stage_id is not None else "-"
    recompute_keys([f"standings:{series_id}:{stage_key}"])

    ours: dict[str, dict[str, Any]] = {}
    for row in TournamentStandings.objects.filter(series_id=series_id, stage_id=stage_id):
        ours[str(row.team_id)] = {
            "played": row.matches_played,
            "won": row.matches_won,
            "lost": row.matches_lost,
            "tied": row.matches_tied,
            "drawn": row.matches_drawn,
            "no_result": row.no_result,
            "points": row.points,
            "nrr": _nrr_for_diff(row.nrr),
        }
    differences = _diff_standings(ours, provider_rows)
    if not differences:
        return None
    job = open_job(
        f"standings:{series_id}:{stage_key}",
        expected={"provider_rows": dict(provider_rows)},
        observed={"pipeline_rows": dict(ours)},
        differences=differences,
    )
    report_mismatch(job, provider=provider)
    return job


def _recompute_leaderboard_key(parts: list[str]) -> None:
    from apps.statistics.services import leaderboard as lb

    scope = parts[2]
    ref = None if parts[3] == "-" else UUID(parts[3])
    fmt = None if parts[4] == "-" else parts[4]
    lb.recompute_player_leaderboard(scope, ref, fmt)


def _recompute_form_key(parts: list[str]) -> None:
    from apps.statistics.services import leaderboard as lb

    player_id = UUID(parts[1])
    fmt = None if parts[2] == "-" else parts[2]
    lb.recompute_form_key(player_id, fmt)


def _recompute_key(key: str) -> None:
    parts = key.split(":")
    kind = parts[0]
    if kind == "career":
        _recompute_career_key(parts)
    elif kind == "team":
        _recompute_team_key(parts)
    elif kind == "h2h":
        _recompute_h2h_key(parts)
    elif kind == "standings":
        _recompute_standings_key(parts)
    elif kind == "leaderboard":
        _recompute_leaderboard_key(parts)
    elif kind == "form":
        _recompute_form_key(parts)


# --------------------------------------------------------------------------- #
# Locking + recompute entry points
# --------------------------------------------------------------------------- #


def _lock_pair(key: str) -> tuple[int, int]:
    """Two signed-32-bit advisory-lock keys for one aggregate key.

    PostgreSQL's two-key advisory-lock form is ``(int, int)`` — the int8
    two-argument overload does not exist — so each hash half is truncated to
    int4 and sign-folded.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()

    def signed(b: bytes) -> int:
        value = int.from_bytes(b, "big")
        return value - (1 << 32) if value >= (1 << 31) else value

    return signed(digest[:4]), signed(digest[4:8])


def _take_advisory_lock(key: str) -> None:
    lock1, lock2 = _lock_pair(key)
    # Explicit ::int: psycopg3 binds Python ints as numeric, and the two-key
    # advisory-lock form only accepts (int, int).
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s::int, %s::int)", [lock1, lock2])


#: Recompute order: source-derived aggregates first, then the leaderboards that
#: read them (a team leaderboard must see the freshly recomputed TeamStat rows).
_KEY_PRIORITY = {
    "career": 0,
    "team": 1,
    "h2h": 2,
    "standings": 3,
    "leaderboard": 4,
    "form": 5,
}


def recompute_keys(keys: Iterable[str]) -> None:
    """Recompute every key, one transaction + advisory lock per key.

    Keys run in dependency order (aggregates before the leaderboards that read
    them), then lexically, so the result is deterministic.
    """
    ordered = sorted(set(keys), key=lambda k: (_KEY_PRIORITY.get(k.split(":")[0], 99), k))
    for key in ordered:
        with transaction.atomic():
            _take_advisory_lock(key)
            _recompute_key(key)


def affected_keys(match: Match) -> set[str]:
    """Union of the match's current dependency set and its previous set."""
    keys = set(dependency_keys_for_match(match))
    watermark = StatisticsWatermark.objects.filter(match_id=match.id).first()
    if watermark is not None:
        keys |= set(watermark.aggregate_keys)
    return keys


def _upsert_watermark(match: Match, keys: set[str]) -> None:
    StatisticsWatermark.objects.update_or_create(
        match_id=match.id,
        defaults={
            "source_digest": _match_source_digest(match),
            "aggregate_keys": sorted(keys),
            "status": "done",
            "recomputed_at": timezone.now(),
        },
    )


def _publish_updated(match: Match) -> None:
    from apps.core import events as core_events

    player_ids = set(
        PlayerMatchStat.objects.filter(match_id=match.id).values_list("player_id", flat=True)
    )
    for pid in sorted(player_ids, key=str):
        core_events.publish(
            core_events.TOPIC_STATS_UPDATED,
            {"match_id": str(match.id), "player_id": str(pid)},
        )
    if match.series_id:
        core_events.publish(
            core_events.TOPIC_STANDINGS_UPDATED,
            {"match_id": str(match.id), "series_id": str(match.series_id)},
        )


def recompute_match(match: Match, *, correction: bool = False) -> list[str]:
    """Recompute a match's dependency set (union with the previous set on
    correction) and record the watermark. Returns the affected keys."""
    keys = set(dependency_keys_for_match(match))
    affected = set(affected_keys(match)) if correction else keys
    if affected:
        recompute_keys(affected)
    _upsert_watermark(match, keys)
    _publish_updated(match)
    return sorted(affected)


# --------------------------------------------------------------------------- #
# Sweep + backfill
# --------------------------------------------------------------------------- #


def sweep_stale_statistics() -> int:
    """Recompute every terminal match whose aggregates are missing or stale."""
    healed = 0
    matches = (
        Match.objects.filter(status=MatchStatus.COMPLETED)
        .filter(Q(player_stats__isnull=False) | Q(result__isnull=False) | Q(innings__isnull=False))
        .distinct()
        .order_by("start_time_utc", "id")
    )
    for match in matches:
        watermark = StatisticsWatermark.objects.filter(match_id=match.id).first()
        current = _match_source_digest(match)
        if watermark is None or watermark.source_digest != current or watermark.status != "done":
            recompute_match(match, correction=watermark is not None)
            healed += 1
    return healed


def _matches_in_scope(
    scope: str, ids: list[str] | None = None, since: str | None = None
) -> list[Match]:
    qs = Match.objects.filter(status=MatchStatus.COMPLETED)
    if scope == "player":
        if not ids:
            raise ValueError("scope 'player' requires --ids")
        qs = qs.filter(player_stats__player_id__in=ids)
    elif scope == "team":
        if not ids:
            raise ValueError("scope 'team' requires --ids")
        qs = qs.filter(teams__team_id__in=ids)
    elif scope == "series":
        if not ids:
            raise ValueError("scope 'series' requires --ids")
        qs = qs.filter(series_id__in=ids)
    elif scope != "all":
        raise ValueError(f"unknown scope: {scope!r}")
    if since:
        qs = qs.filter(source_local_date__gte=since)
    return list(qs.distinct().order_by("start_time_utc", "id"))


def rebuild_statistics(
    scope: str = "all",
    *,
    dry_run: bool = False,
    ids: list[str] | None = None,
    since: str | None = None,
) -> int:
    """Full recompute of every match in scope (dry-run reports only)."""
    matches = _matches_in_scope(scope, ids, since)
    count = 0
    for match in matches:
        count += 1
        if not dry_run:
            recompute_match(match, correction=True)
    return count


# --------------------------------------------------------------------------- #
# Event wiring
# --------------------------------------------------------------------------- #


def _recompute_from_event(payload: Any, *, correction: bool) -> None:
    match_id = payload.get("match_id")
    if not match_id:
        return
    match = Match.objects.filter(id=match_id).first()
    if match is None:
        return
    recompute_match(match, correction=correction)


def _repoint_source_rows(winner_id: str, loser_id: str) -> None:
    """Re-point the loser's per-match lines, merging same-match rows.

    The merge registry's naive FK re-point cannot be used for
    ``player_match_stat``: when both identities played the same match the
    UPDATE would violate ``ux_player_match_stat (match_id, player_id)``. So the
    subscriber owns the re-point: a row whose match the winner already has a
    row in has its batting/bowling lines merged into the winner's row.
    """
    loser_rows = list(PlayerMatchStat.objects.filter(player_id=loser_id).order_by("match_id", "id"))
    for row in loser_rows:
        existing = PlayerMatchStat.objects.filter(
            match_id=row.match_id, player_id=winner_id
        ).first()
        if existing is None:
            PlayerMatchStat.objects.filter(id=row.id).update(player_id=winner_id)
            continue
        existing.batting = list(existing.batting) + list(row.batting)
        existing.bowling = list(existing.bowling) + list(row.bowling)
        existing.did_bat = existing.did_bat or row.did_bat
        existing.did_bowl = existing.did_bowl or row.did_bowl
        existing.is_partial = existing.is_partial or row.is_partial
        existing.revision += 1
        existing.save(
            update_fields=[
                "batting",
                "bowling",
                "did_bat",
                "did_bowl",
                "is_partial",
                "revision",
            ]
        )
        row.delete()


def _on_player_merged(payload: Any) -> None:
    """Re-point source rows, drop the loser's aggregates, recompute the winner.

    Runs synchronously inside the merge transaction (``player.merged`` is
    published ``on_commit=False``), so a fresh recompute-from-source yields the
    merged totals and any failure rolls the merge back.
    """
    winner_id = payload.get("canonical_player_id")
    loser_id = payload.get("merged_player_id")
    if not winner_id or not loser_id:
        return
    _repoint_source_rows(winner_id, loser_id)
    PlayerCareerStat.objects.filter(player_id=loser_id).delete()
    FormTrend.objects.filter(player_id=loser_id).delete()
    Leaderboard.objects.filter(player_id=loser_id).delete()
    from apps.rankings.models import PlayerRanking

    PlayerRanking.objects.filter(player_id=loser_id).delete()

    match_ids = list(
        PlayerMatchStat.objects.filter(player_id=winner_id)
        .values_list("match_id", flat=True)
        .distinct()
    )
    for match_id in match_ids:
        match = Match.objects.filter(id=match_id).first()
        if match is not None:
            recompute_match(match, correction=True)


def register() -> None:
    """Wire the Tier-2 recompute pipeline to the match lifecycle + merge events."""
    from apps.core import events as core_events

    core_events.subscribe(
        core_events.TOPIC_MATCH_COMPLETED, lambda p: _recompute_from_event(p, correction=False)
    )
    core_events.subscribe(
        core_events.TOPIC_CORRECTION_APPLIED, lambda p: _recompute_from_event(p, correction=True)
    )
    core_events.subscribe(core_events.TOPIC_PLAYER_MERGED, _on_player_merged)
    # NOTE: ``player_match_stat`` is deliberately NOT registered with the merge
    # registry (ledger decision 9, refined): the generic FK re-point would
    # violate ux_player_match_stat when both identities played the same match.
    # ``_on_player_merged`` re-points/merges the source rows itself. The
    # aggregate FKs stay unregistered too — their expression uniques would be
    # violated by a re-point — and their rows are deleted + recomputed here.


__all__ = [
    "affected_keys",
    "dependency_keys_for_match",
    "rebuild_statistics",
    "recompute_keys",
    "recompute_match",
    "reconcile_standings",
    "register",
    "sweep_stale_statistics",
]
