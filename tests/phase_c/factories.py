"""Small helpers for Phase C tests: build teams, players, matches, innings.

Kept deliberately lightweight (no factory_boy classes yet) so scenario tests
can construct fixtures inline without extra indirection.
"""

from __future__ import annotations

import datetime
from datetime import UTC

from apps.core.enums import FormatType, TeamType
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
