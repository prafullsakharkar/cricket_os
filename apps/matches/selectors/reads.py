"""Read selectors for the ``/matches/{id}`` read API (CRICKET_API_SPEC §3/§4).

Every query reads **current rows only** (``delivery.is_current=True``) and
filters the partitioned ``delivery`` table by its partition key
(``match_date``, always the match START date) so the planner prunes to the
match's year partition. The four endpoints:

* :func:`match_shell` — match + state + team scores (spec §4.1);
* :func:`list_innings` — innings list & progression;
* :func:`match_scorecard` — innings-indexed cards + FOW + partnerships +
  merged retirements, with ``meta.ball_data`` / ``meta.revision`` /
  ``meta.commentary`` (spec §4.2);
* :func:`page_deliveries` — partition-pruned ball-by-ball, cursor on
  ``delivery_seq`` (unique per innings, so no pk tiebreaker needed).

The selectors build plain dictionaries; the API layer wraps them in the
standard envelope and cache headers.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from django.utils import timezone

from apps.core.enums import MatchStatus
from apps.core.errors import NotFound
from apps.deliveries.models import Delivery
from apps.innings.models import Innings, InningsEvent, InningsSummary
from apps.matches.models import Match, MatchResult, MatchTeam, Toss
from apps.players.models import Player
from apps.providers.licensing import attribution_for, license_meta
from apps.providers.models import DataProvider
from apps.scorecards.models import BattingEntry, BowlingEntry, FallOfWicket, Partnership
from apps.teams.models import Team

#: Match lifecycle states treated as finished (completed sub-resources cache
#: much harder than live ones — API_SPECIFICATION §3.9).
TERMINAL_STATUSES = frozenset({"COMPLETED", "ABANDONED", "CANCELLED", "FORFEITED", "POSTPONED"})

#: Statuses reported under ``/matches/live``: play or paused-in-play (mirrors
#: the poll dispatcher's ``LIVE_STATUSES`` without importing live services).
LIVE_LIST_STATUSES = frozenset(
    {
        MatchStatus.TOSS,
        MatchStatus.IN_PROGRESS,
        MatchStatus.INNINGS_BREAK,
        MatchStatus.DRINKS,
        MatchStatus.RAIN_DELAY,
        MatchStatus.BAD_LIGHT,
        MatchStatus.STUMPS,
        MatchStatus.DAY_BREAK,
    }
)

#: Default look-ahead/look-back windows for the fixture feeds (days).
DEFAULT_UPCOMING_DAYS = 7
DEFAULT_RECENT_DAYS = 7

_RETIREMENT_EVENTS = ("RETIREMENT_HURT", "RETIREMENT_NOT_OUT")

_ORDER = ("innings_number", "is_super_over", "super_over_number")


def get_match(pk: str) -> Match:
    """The match with the given UUID, or :class:`NotFound`."""
    try:
        return Match.objects.select_related("venue").get(pk=pk)
    except Match.DoesNotExist as exc:
        raise NotFound(f"Match {pk} not found") from exc


def _match_date(match: Match) -> datetime.date:
    """The partition key for the match's deliveries (the start date)."""
    start = match.start_time_utc
    return start.date() if isinstance(start, datetime.datetime) else start


# --------------------------------------------------------------------------- #
# Small payload builders
# --------------------------------------------------------------------------- #


def _team_brief(team: Team | None) -> dict[str, Any] | None:
    if team is None:
        return None
    return {"id": str(team.id), "name": team.name, "abbreviation": team.abbreviation}


def _player_brief(player: Player | None) -> dict[str, Any] | None:
    if player is None:
        return None
    return {"id": str(player.id), "name": player.full_name}


def _totals(summary: InningsSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "runs": summary.runs,
        "wickets": summary.wickets,
        "overs": summary.overs_display,
        "legal_balls": summary.legal_balls,
        "extras": summary.extras or {},
        "run_rate": float(summary.run_rate) if summary.run_rate is not None else None,
    }


def _meta_for(match: Match, *, ball_data: bool) -> dict[str, Any]:
    return {
        "ball_data": ball_data,
        "commentary": False,  # commentary rights are not licensed (spec §3.3)
        "revision": match.revision,
    }


# --------------------------------------------------------------------------- #
# Match shell + innings list
# --------------------------------------------------------------------------- #


def _innings_queryset(match: Match) -> Any:
    return Innings.objects.filter(match=match).order_by(*_ORDER)


def _shell(
    match: Match,
    match_teams: list[Any],
    innings: list[Any],
    summaries: dict[Any, InningsSummary | None],
    result: MatchResult | None,
    toss: Toss | None,
) -> dict[str, Any]:
    """Render the shared match-summary payload from already-fetched rows.

    ``match_shell`` (single match) and the list selectors (batch) both build
    the exact same payload through here, so an item in ``/matches/live`` is
    byte-for-byte the ``GET /matches/{id}/`` shell.
    """
    latest_by_team: dict[UUID, InningsSummary | None] = {}
    for db_innings in innings:
        latest_by_team[db_innings.batting_team_id] = summaries.get(db_innings.id)

    teams_payload: list[dict[str, Any]] = []
    for mt in match_teams:
        summary = latest_by_team.get(mt.team_id)
        teams_payload.append(
            {
                "team": _team_brief(mt.team),
                "score": _totals(summary),
            }
        )

    return {
        "id": str(match.id),
        "format": match.format_type,
        "gender": match.gender,
        "status": match.status,
        "status_detail": match.status_detail,
        "result": {
            "type": result.result_type if result else None,
            "winner": _team_brief(result.winner_team) if result else None,
            "margin": {"type": result.margin_type, "value": result.margin_value}
            if result and result.margin_type
            else None,
            "text": result.result_text if result else None,
        },
        "toss": {
            "winner": _team_brief(toss.winner_team) if toss else None,
            "decision": toss.decision if toss else None,
        },
        "teams": teams_payload,
        "venue": {
            "name": match.venue.name if match.venue else None,
            "city": None,
        },
        "start_time": match.start_time_utc.isoformat(),
        "source": {
            "local_date": match.source_local_date.isoformat(),
            "timezone": match.source_timezone,
        },
        "overs_limit": match.overs_limit,
        "revision": match.revision,
        "updated_at": match.provider_updated_at.isoformat() if match.provider_updated_at else None,
    }


def match_shell(match: Match) -> dict[str, Any]:
    """The match shell + state with each team's latest innings score."""
    match_teams = list(match.teams.select_related("team").order_by("team_number"))
    innings = list(_innings_queryset(match))
    # Reverse one-to-one: fetch summaries by id rather than relying on
    # ``select_related`` descriptor semantics (absent rows raise otherwise).
    summaries: dict[Any, InningsSummary | None] = {
        row.innings_id: row
        for row in InningsSummary.objects.filter(innings__in=[i.id for i in innings])
    }
    result = MatchResult.objects.filter(match=match).select_related("winner_team").first()
    toss = Toss.objects.filter(match=match).select_related("winner_team").first()
    return _shell(match, match_teams, innings, summaries, result, toss)


def list_innings(match: Match) -> dict[str, Any]:
    """The innings list & progression for a match."""
    innings = list(
        _innings_queryset(match).select_related("batting_team", "bowling_team", "summary")
    )
    ball_data = _has_ball_data(match)
    return {
        "match_id": str(match.id),
        "meta": _meta_for(match, ball_data=ball_data),
        "innings": [
            {
                "innings_number": db_innings.innings_number,
                "is_super_over": db_innings.is_super_over,
                "batting_team": _team_brief(db_innings.batting_team),
                "bowling_team": _team_brief(db_innings.bowling_team),
                "status": db_innings.status,
                "declared": db_innings.declared,
                "all_out": db_innings.all_out,
                "target": db_innings.target,
                "totals": _totals(db_innings.summary),
            }
            for db_innings in innings
        ],
    }


# --------------------------------------------------------------------------- #
# Scorecard
# --------------------------------------------------------------------------- #


def _has_ball_data(match: Match) -> bool:
    return Delivery.objects.filter(
        match_id=match.id,
        match_date=_match_date(match),
        is_current=True,
    ).exists()


def _batting_payload(entries: list[BattingEntry]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for entry in entries:
        dismissal = None
        if entry.is_out:
            fielders = [
                f
                for f in (
                    _player_brief(entry.fielder1),
                    _player_brief(entry.fielder2),
                    _player_brief(entry.fielder3),
                )
                if f
            ]
            dismissal = {
                "type": entry.dismissal_type,
                "bowler": _player_brief(entry.bowler),
                "fielders": fielders,
            }
        payload.append(
            {
                "position": entry.position,
                "player": _player_brief(entry.player),
                "runs": entry.runs,
                "balls": entry.balls,
                "fours": entry.fours,
                "sixes": entry.sixes,
                "dots": entry.dots,
                "strike_rate": float(entry.strike_rate) if entry.strike_rate is not None else None,
                "is_out": entry.is_out,
                "dismissal": dismissal,
            }
        )
    return payload


def _bowling_payload(entries: list[BowlingEntry]) -> list[dict[str, Any]]:
    return [
        {
            "player": _player_brief(entry.player),
            "overs": entry.overs_display,
            "legal_balls": entry.legal_balls,
            "maidens": entry.maidens,
            "runs_conceded": entry.runs_conceded,
            "wickets": entry.wickets,
            "wides": entry.wides,
            "noballs": entry.noballs,
            "economy": float(entry.economy) if entry.economy is not None else None,
        }
        for entry in entries
    ]


def _fow_payload(rows: list[FallOfWicket]) -> list[dict[str, Any]]:
    return [
        {
            "wicket": row.wicket_number,
            "score": row.score,
            "over": row.over_display,
            "player": _player_brief(row.dismissed_player),
        }
        for row in rows
    ]


def _partnership_payload(rows: list[Partnership]) -> list[dict[str, Any]]:
    return [
        {
            "wicket": row.wicket_number,
            "runs": row.runs,
            "balls": row.balls,
            "batters": [
                str(row.batter1_id) if row.batter1_id else None,
                str(row.batter2_id) if row.batter2_id else None,
            ],
            "unbroken": row.unbroken,
        }
        for row in rows
    ]


def _retirements(match: Match) -> dict[UUID, list[dict[str, Any]]]:
    """Ball-anchored + off-ball retirements merged per innings (spec §3.2)."""
    by_innings: dict[UUID, list[dict[str, Any]]] = {}
    ball_rows = (
        Delivery.objects.filter(
            match_id=match.id,
            match_date=_match_date(match),
            is_current=True,
            retired_player_id__isnull=False,
        )
        .select_related("retired_player")
        .order_by("delivery_seq")
    )
    for delivery in ball_rows:
        by_innings.setdefault(delivery.innings_id, []).append(
            {
                "player": _player_brief(delivery.retired_player),
                "type": delivery.retirement_type,
                "anchor": {
                    "delivery_seq": delivery.delivery_seq,
                    "over": str(delivery.over_number),
                },
                "resumed": False,
            }
        )
    event_rows = (
        InningsEvent.objects.filter(
            innings__match=match,
            is_current=True,
            event_type__in=_RETIREMENT_EVENTS,
        )
        .select_related("player")
        .order_by("anchor_delivery_seq")
    )
    for event in event_rows:
        by_innings.setdefault(event.innings_id, []).append(
            {
                "player": _player_brief(event.player),
                "type": event.event_type.removeprefix("RETIREMENT_"),
                "anchor": (
                    {"delivery_seq": event.anchor_delivery_seq, "over": str(event.over_number)}
                    if event.anchor_delivery_seq is not None
                    else None
                ),
                "resumed": False,
            }
        )
    return by_innings


def match_scorecard(match: Match, *, innings_number: int | None = None) -> dict[str, Any]:
    """Innings-indexed scorecard (spec §4.2)."""
    ball_data = _has_ball_data(match)
    retirements = _retirements(match)

    innings_qs = _innings_queryset(match).select_related("batting_team", "bowling_team", "summary")
    if innings_number is not None:
        innings_qs = innings_qs.filter(innings_number=innings_number)
    innings = list(innings_qs)

    # Fetch each card section per innings with a single prefetch each.
    innings_by_id = {db_innings.id: db_innings for db_innings in innings}
    batting: dict[UUID, list[BattingEntry]] = {i.id: [] for i in innings}
    bowling: dict[UUID, list[BowlingEntry]] = {i.id: [] for i in innings}
    fow: dict[UUID, list[FallOfWicket]] = {i.id: [] for i in innings}
    partnerships: dict[UUID, list[Partnership]] = {i.id: [] for i in innings}

    ids = list(innings_by_id)
    for batting_entry in (
        BattingEntry.objects.filter(innings_id__in=ids)
        .select_related("player", "bowler", "fielder1", "fielder2", "fielder3")
        .order_by("position", "player__full_name")
    ):
        batting[batting_entry.innings_id].append(batting_entry)
    for bowling_entry in (
        BowlingEntry.objects.filter(innings_id__in=ids)
        .select_related("player")
        .order_by("player__full_name")
    ):
        bowling[bowling_entry.innings_id].append(bowling_entry)
    for fow_row in (
        FallOfWicket.objects.filter(innings_id__in=ids)
        .select_related("dismissed_player")
        .order_by("wicket_number")
    ):
        fow[fow_row.innings_id].append(fow_row)
    for partnership_row in (
        Partnership.objects.filter(innings_id__in=ids)
        .select_related("batter1", "batter2")
        .order_by("wicket_number")
    ):
        partnerships[partnership_row.innings_id].append(partnership_row)

    innings_payload = []
    for db_innings in innings:
        innings_payload.append(
            {
                "innings_number": db_innings.innings_number,
                "is_super_over": db_innings.is_super_over,
                "batting_team": _team_brief(db_innings.batting_team),
                "bowling_team": _team_brief(db_innings.bowling_team),
                "totals": _totals(db_innings.summary),
                "batting": _batting_payload(batting[db_innings.id]),
                "bowling": _bowling_payload(bowling[db_innings.id]),
                "fall_of_wickets": _fow_payload(fow[db_innings.id]),
                "partnerships": _partnership_payload(partnerships[db_innings.id]),
                "retirements": retirements.get(db_innings.id, []),
            }
        )

    return {
        "match_id": str(match.id),
        "meta": _meta_for(match, ball_data=ball_data),
        "innings": innings_payload,
    }


# --------------------------------------------------------------------------- #
# Deliveries (partition-pruned, cursor on delivery_seq)
# --------------------------------------------------------------------------- #


def deliveries_queryset(
    match: Match,
    innings: Innings,
    *,
    from_seq: int | None = None,
    to_seq: int | None = None,
    over: int | None = None,
) -> Any:
    """Current deliveries of one innings, pruned to the match's partition.

    ``match_date`` is the partition key — including it lets the planner scan
    only the match's year partition (DATABASE_SCHEMA §3.6).
    """
    qs = Delivery.objects.filter(
        match_id=match.id,
        match_date=_match_date(match),
        innings_id=innings.id,
        is_current=True,
    )
    if from_seq is not None:
        qs = qs.filter(delivery_seq__gte=from_seq)
    if to_seq is not None:
        qs = qs.filter(delivery_seq__lte=to_seq)
    if over is not None:
        qs = qs.filter(over_number=over)
    return qs


def _delivery_payload(delivery: Delivery) -> dict[str, Any]:
    runs = (
        delivery.runs_off_bat
        + delivery.wides
        + delivery.noballs
        + delivery.byes
        + delivery.leg_byes
        + delivery.penalty_runs
    )
    wicket = None
    if delivery.is_wicket:
        fielders = [
            f
            for f in (
                _player_brief(delivery.fielder1),
                _player_brief(delivery.fielder2),
                _player_brief(delivery.fielder3),
            )
            if f
        ]
        wicket = {
            "type": delivery.dismissal_type,
            "player": _player_brief(delivery.dismissed_player),
            "bowler_credited": delivery.bowler_credited_wicket,
            "fielders": fielders,
        }
    retirement = (
        {"type": delivery.retirement_type, "player": _player_brief(delivery.retired_player)}
        if delivery.retired_player_id
        else None
    )
    return {
        "delivery_seq": delivery.delivery_seq,
        "over": delivery.over_number,
        "ball": delivery.ball_in_over,
        "bowler": _player_brief(delivery.bowler),
        "batter": _player_brief(delivery.batter),
        "non_striker": _player_brief(delivery.non_striker),
        "runs": {
            "total": runs,
            "batter": delivery.runs_off_bat,
            "extras": runs - delivery.runs_off_bat,
            "wides": delivery.wides,
            "noballs": delivery.noballs,
            "byes": delivery.byes,
            "leg_byes": delivery.leg_byes,
            "penalty": delivery.penalty_runs,
        },
        "wicket": wicket,
        "retirement": retirement,
        "revision": delivery.revision,
    }


def get_innings(match: Match, innings_number: int) -> Innings:
    try:
        return Innings.objects.get(match=match, innings_number=innings_number, is_super_over=False)
    except Innings.DoesNotExist as exc:
        raise NotFound(f"Match {match.id} has no innings {innings_number}") from exc


# --------------------------------------------------------------------------- #
# Match list feeds (API_SPEC §3.1: /matches/live|upcoming|recent)
# --------------------------------------------------------------------------- #


def _match_filters(
    *,
    format_type: str | None = None,
    gender: str | None = None,
    series_id: Any = None,
    team_id: Any = None,
) -> Any:
    """Shared ``?format=&gender=&series=&team=`` filtering (all optional)."""
    qs = Match.objects.select_related("venue")
    if format_type:
        qs = qs.filter(format_type=format_type)
    if gender:
        qs = qs.filter(gender=gender)
    if series_id:
        qs = qs.filter(series_id=series_id)
    if team_id:
        qs = qs.filter(teams__team_id=team_id)
    return qs


def list_live_matches(
    *,
    format_type: str | None = None,
    gender: str | None = None,
    series_id: Any = None,
    team_id: Any = None,
) -> Any:
    """Currently-live matches, newest start first (API_SPEC §3.1)."""
    return (
        _match_filters(format_type=format_type, gender=gender, series_id=series_id, team_id=team_id)
        .filter(status__in=LIVE_LIST_STATUSES)
        .order_by("-start_time_utc", "-id")
    )


def list_upcoming_matches(
    *,
    days: int = DEFAULT_UPCOMING_DAYS,
    format_type: str | None = None,
    gender: str | None = None,
    series_id: Any = None,
    team_id: Any = None,
) -> Any:
    """Scheduled fixtures starting within the next ``days`` (soonest first)."""
    now = timezone.now()
    return (
        _match_filters(format_type=format_type, gender=gender, series_id=series_id, team_id=team_id)
        .filter(
            status=MatchStatus.SCHEDULED,
            start_time_utc__gte=now,
            start_time_utc__lt=now + datetime.timedelta(days=days),
        )
        .order_by("start_time_utc", "id")
    )


def list_recent_matches(
    *,
    days: int = DEFAULT_RECENT_DAYS,
    format_type: str | None = None,
    gender: str | None = None,
    series_id: Any = None,
    team_id: Any = None,
) -> Any:
    """Terminal matches finished within the last ``days`` (newest first)."""
    cutoff = timezone.now() - datetime.timedelta(days=days)
    return (
        _match_filters(format_type=format_type, gender=gender, series_id=series_id, team_id=team_id)
        .filter(status__in=TERMINAL_STATUSES, end_time_utc__gte=cutoff)
        .order_by("-end_time_utc", "-id")
    )


def match_summaries(
    matches: Sequence[Match],
    *,
    stale_codes: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Batch-render match summaries for a page (constant queries, no N+1).

    Fetches teams, innings, summaries, results, tosses and provider codes in a
    handful of ``__in`` queries, then renders each item through the same
    ``_shell`` builder as ``GET /matches/{id}/``. Each item additionally
    carries the licence flags (``ball_data``/``commentary``/``attribution``)
    and ``stale`` (True when the item's provider breaker is OPEN — the API
    serves last-known-good rather than failing, SECURITY §8).
    """
    ids = [m.id for m in matches]
    stale = frozenset(stale_codes)

    match_teams: dict[UUID, list[Any]] = {}
    for mt in (
        MatchTeam.objects.filter(match_id__in=ids)
        .select_related("team")
        .order_by("match_id", "team_number")
    ):
        match_teams.setdefault(mt.match_id, []).append(mt)

    innings_by_match: dict[UUID, list[Any]] = {}
    for db_innings in Innings.objects.filter(match_id__in=ids).order_by("match_id", *_ORDER):
        innings_by_match.setdefault(db_innings.match_id, []).append(db_innings)

    summaries: dict[Any, InningsSummary | None] = {
        row.innings_id: row for row in InningsSummary.objects.filter(innings__match_id__in=ids)
    }
    results = {
        row.match_id: row
        for row in MatchResult.objects.filter(match_id__in=ids).select_related("winner_team")
    }
    tosses = {
        row.match_id: row
        for row in Toss.objects.filter(match_id__in=ids).select_related("winner_team")
    }

    provider_ids = {m.source_provider_id for m in matches if m.source_provider_id}
    # Keyed by UUID but looked up with ``match.source_provider_id`` (UUID | None):
    # a None key simply misses and the item renders with ``source=None``.
    code_by_id: dict[Any, str] = dict(
        DataProvider.objects.filter(id__in=provider_ids).values_list("id", "code")
    )

    items: list[dict[str, Any]] = []
    for match in matches:
        code = code_by_id.get(match.source_provider_id)
        item = _shell(
            match,
            match_teams.get(match.id, []),
            innings_by_match.get(match.id, []),
            summaries,
            results.get(match.id),
            tosses.get(match.id),
        )
        if code is not None:
            meta = license_meta(code)
            item["stale"] = code in stale
            item["ball_data"] = meta["ball_data"]
            item["commentary"] = meta["commentary"]
            item["attribution"] = attribution_for(code)
        else:
            item["stale"] = False
            item["ball_data"] = True
            item["commentary"] = False
            item["attribution"] = None
        items.append(item)
    return items


__all__ = [
    "DEFAULT_RECENT_DAYS",
    "DEFAULT_UPCOMING_DAYS",
    "LIVE_LIST_STATUSES",
    "TERMINAL_STATUSES",
    "_delivery_payload",
    "deliveries_queryset",
    "get_innings",
    "get_match",
    "list_innings",
    "list_live_matches",
    "list_recent_matches",
    "list_upcoming_matches",
    "match_scorecard",
    "match_shell",
    "match_summaries",
]
