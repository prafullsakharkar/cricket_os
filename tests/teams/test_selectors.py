"""Team selectors: trigram ranking, soft-delete defaults, membership temporality."""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.enums import TeamType
from apps.core.errors import NotFound
from apps.players.models import Player
from apps.teams.models import Team, TeamMembership
from apps.teams.selectors import get_team, list_players_of_team, list_teams

pytestmark = pytest.mark.django_db


def make_team(**overrides: object) -> Team:
    defaults: dict[str, object] = {"name": "Atlantis Royals", "team_type": TeamType.DOMESTIC}
    defaults.update(overrides)
    return Team.objects.create(**defaults)  # type: ignore[arg-type]


def make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Player One"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


class TestListTeams:
    def test_excludes_inactive_and_deleted(self) -> None:
        visible = make_team(name="Atlantis Royals")
        make_team(name="Avalon Kings", is_active=False)
        make_team(
            name="Avalon Queens", deleted_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        )
        assert list(list_teams()) == [visible]

    def test_trigram_q_ranks_best_match_first(self) -> None:
        make_team(name="Mumbai Indians")
        make_team(name="Mumbai Mavericks")
        make_team(name="Chennai Super Kings")
        ranked = list(list_teams(q="Mumbai"))
        assert [t.name for t in ranked] == ["Mumbai Indians", "Mumbai Mavericks"]

    def test_category_filter(self) -> None:
        make_team(name="Atlantis Royals", team_type=TeamType.DOMESTIC)
        make_team(name="Atlantis International", team_type=TeamType.INTERNATIONAL)
        assert list(list_teams(category=TeamType.DOMESTIC).values_list("name", flat=True)) == [
            "Atlantis Royals"
        ]

    def test_get_team_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_team("00000000-0000-7000-8000-000000000000")


class TestGetTeam:
    def test_prefetches_memberships_n1_free(self) -> None:
        team = make_team()
        players = [make_player(full_name=f"Player {i}") for i in range(3)]
        for player in players:
            TeamMembership.objects.create(team=team, player=player)

        with CaptureQueriesContext(connection) as queries:
            result = get_team(team.pk)
        assert result.memberships.count() == 3
        assert len(queries) == 2  # team + one memberships query


class TestMembershipTemporality:
    def _setup(self) -> tuple[Team, Player, Player]:
        team = make_team()
        player_a = make_player(full_name="Player A")
        player_b = make_player(full_name="Player B")
        TeamMembership.objects.create(
            team=team,
            player=player_a,
            joined_at=datetime.date(2015, 1, 1),
            left_at=datetime.date(2018, 12, 31),
        )
        TeamMembership.objects.create(
            team=team, player=player_b, joined_at=datetime.date(2019, 1, 1)
        )
        return team, player_a, player_b

    def test_as_of_returns_active_player(self) -> None:
        team, player_a, _ = self._setup()
        assert list(list_players_of_team(team.pk, as_of=datetime.date(2016, 6, 1))) == [player_a]

    def test_left_at_is_exclusive(self) -> None:
        team, _, _ = self._setup()
        # The player left on 2018-12-31, so they are NOT active that day.
        assert list(list_players_of_team(team.pk, as_of=datetime.date(2018, 12, 31))) == []

    def test_rejoin_yields_both_rows_across_dates(self) -> None:
        team, player_a, player_b = self._setup()
        # On the first player's final day they are gone; the second is active.
        assert list(list_players_of_team(team.pk, as_of=datetime.date(2018, 12, 30))) == [player_a]
        assert list(list_players_of_team(team.pk, as_of=datetime.date(2019, 1, 1))) == [player_b]
        assert list(list_players_of_team(team.pk, as_of=datetime.date(2020, 1, 1))) == [player_b]
        # Both memberships exist in the history (2 open-or-closed rows).
        assert team.memberships.count() == 2

    def test_default_as_of_returns_only_open_memberships(self) -> None:
        team, player_a, player_b = self._setup()
        # Without as_of only the never-left membership is returned.
        assert list(list_players_of_team(team.pk)) == [player_b]
        assert player_a not in list(list_players_of_team(team.pk))
