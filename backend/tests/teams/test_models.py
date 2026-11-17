"""Team-domain model constraints (DATABASE_SCHEMA §3.3–3.4)."""

from __future__ import annotations

import datetime

import pytest
from django.db import IntegrityError

from apps.core.enums import TeamType
from apps.players.models import Player
from apps.teams.models import Squad, SquadMember, Team, TeamMembership

pytestmark = pytest.mark.django_db


def make_team(**overrides: object) -> Team:
    defaults: dict[str, object] = {"name": "India", "team_type": TeamType.INTERNATIONAL}
    defaults.update(overrides)
    return Team.objects.create(**defaults)  # type: ignore[arg-type]


def make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Virat Kohli"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


class TestTeam:
    def test_duplicate_provider_id_rejected(self) -> None:
        provider_uuid = "11111111-1111-7111-8111-111111111111"
        make_team(source_provider_id=provider_uuid, provider_team_id="t1")
        with pytest.raises(IntegrityError):
            make_team(source_provider_id=provider_uuid, provider_team_id="t1")

    def test_null_provider_ids_allowed_many_times(self) -> None:
        make_team()
        make_team(name="Australia")
        assert Team.objects.count() == 2

    def test_team_type_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_team(team_type="galactic")

    def test_gender_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            make_team(gender="Z")


class TestTeamMembership:
    """Membership temporality: one open membership per (team, player)."""

    def test_two_open_memberships_rejected(self) -> None:
        team = make_team()
        player = make_player()
        TeamMembership.objects.create(team=team, player=player)
        with pytest.raises(IntegrityError):
            TeamMembership.objects.create(team=team, player=player)

    def test_distinct_left_at_allowed(self) -> None:
        team = make_team()
        player = make_player()
        TeamMembership.objects.create(
            team=team,
            player=player,
            joined_at=datetime.date(2015, 1, 1),
            left_at=datetime.date(2018, 12, 31),
        )
        TeamMembership.objects.create(
            team=team,
            player=player,
            joined_at=datetime.date(2019, 1, 1),
            left_at=datetime.date(2021, 12, 31),
        )
        assert team.memberships.count() == 2

    def test_rejoin_after_leaving_allowed(self) -> None:
        """Closed row keeps its left_at; a new open row does not clash with it."""
        team = make_team()
        player = make_player()
        TeamMembership.objects.create(team=team, player=player, left_at=datetime.date(2018, 12, 31))
        TeamMembership.objects.create(team=team, player=player, joined_at=datetime.date(2019, 1, 1))
        assert team.memberships.count() == 2
        open_rows = team.memberships.filter(left_at__isnull=True)
        assert open_rows.count() == 1

    def test_membership_type_check_constraint(self) -> None:
        team = make_team()
        player = make_player()
        with pytest.raises(IntegrityError):
            TeamMembership.objects.create(team=team, player=player, membership_type="banana")


class TestSquadAndMember:
    def test_squad_member_unique(self) -> None:
        team = make_team()
        player = make_player()
        squad = Squad.objects.create(team=team)
        SquadMember.objects.create(squad=squad, player=player)
        with pytest.raises(IntegrityError):
            SquadMember.objects.create(squad=squad, player=player)

    def test_squad_member_status_check(self) -> None:
        team = make_team()
        player = make_player()
        squad = Squad.objects.create(team=team)
        with pytest.raises(IntegrityError):
            SquadMember.objects.create(squad=squad, player=player, status="benched")
