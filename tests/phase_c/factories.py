"""Small helpers for Phase C tests: build teams, players, matches, innings.

Kept deliberately lightweight (no factory_boy classes yet) so scenario tests
can construct fixtures inline without extra indirection.
"""

from __future__ import annotations

import datetime
from datetime import UTC

from apps.core.enums import FormatType, TeamType
from apps.innings.models import Innings
from apps.matches.models import Match, MatchTeam
from apps.players.models import Player
from apps.teams.models import Team

MATCH_DATE = datetime.date(2026, 10, 25)


def make_team(name: str = "India", **overrides: object) -> Team:
    defaults: dict[str, object] = {"name": name, "team_type": TeamType.INTERNATIONAL}
    defaults.update(overrides)
    return Team.objects.create(**defaults)  # type: ignore[arg-type]


def make_player(full_name: str = "Virat Kohli", **overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": full_name}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


def make_match(
    start_date: datetime.date = MATCH_DATE,
    format_type: str = FormatType.T20I,
    **overrides: object,
) -> Match:
    defaults: dict[str, object] = {
        "format_type": format_type,
        "start_time_utc": datetime.datetime.combine(start_date, datetime.time(9, 0), tzinfo=UTC),
        "source_local_date": start_date,
        "source_timezone": "Asia/Kolkata",
    }
    defaults.update(overrides)
    return Match.objects.create(**defaults)  # type: ignore[arg-type]


def make_match_teams(match: Match, team1: Team, team2: Team) -> None:
    MatchTeam.objects.create(match=match, team=team1, team_number=1)
    MatchTeam.objects.create(match=match, team=team2, team_number=2)


def make_innings(
    match: Match,
    team1: Team,
    team2: Team,
    innings_number: int = 1,
    **overrides: object,
) -> Innings:
    defaults: dict[str, object] = {
        "match": match,
        "innings_number": innings_number,
        "batting_team": team1,
        "bowling_team": team2,
    }
    defaults.update(overrides)
    return Innings.objects.create(**defaults)  # type: ignore[arg-type]


def make_provider(code: str = "cricsheet", **overrides: object) -> object:
    """Return the DataProvider for ``code``, creating it only if absent.

    ``cricsheet`` is seeded by providers.0002_seed_cricsheet_provider, so
    re-running the same code across tests must not violate ux_data_provider_code.
    """
    from apps.providers.models import DataProvider

    defaults: dict[str, object] = {"name": code.title(), "code": code}
    defaults.update(overrides)
    return DataProvider.objects.get_or_create(code=code, defaults={**defaults, **overrides})[0]
