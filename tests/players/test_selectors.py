"""Player selectors: trigram ranking, filters, duplicate queue, N+1-free."""

from __future__ import annotations

import datetime
import decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.enums import PlayerRole, TeamType
from apps.core.errors import NotFound
from apps.players.models import Player, PlayerDuplicateCandidate
from apps.players.selectors import get_player, list_duplicate_candidates, list_players
from apps.teams.models import Team, TeamMembership
from apps.venues.models import Country

pytestmark = pytest.mark.django_db


def make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Player One"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


class TestListPlayers:
    def test_excludes_inactive_and_deleted(self) -> None:
        visible = make_player(full_name="Active Player")
        make_player(full_name="Inactive Player", is_active=False)
        make_player(
            full_name="Deleted Player",
            deleted_at=datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
        )
        assert list(list_players()) == [visible]

    def test_trigram_q_ranks_best_match_first(self) -> None:
        make_player(full_name="Virat Kohli")
        make_player(full_name="Virat Kohli Junior")
        make_player(full_name="Steve Smith")
        ranked = list(list_players(q="Virat Kohli"))
        assert [p.full_name for p in ranked] == ["Virat Kohli", "Virat Kohli Junior"]

    def test_filters_by_team_open_membership(self) -> None:
        team = Team.objects.create(name="Atlantis XI", team_type=TeamType.INTERNATIONAL)
        member = make_player(full_name="Member Player")
        TeamMembership.objects.create(team=team, player=member)
        ex_member = make_player(full_name="Ex Member")
        TeamMembership.objects.create(
            team=team, player=ex_member, left_at=datetime.date(2020, 1, 1)
        )
        make_player(full_name="Other Player")
        assert list(list_players(team=str(team.pk))) == [member]

    def test_filters_by_country_and_role(self) -> None:
        country = Country.objects.create(name="Atlantis")
        batter = make_player(
            full_name="Atlantis Batter", birth_country=country, primary_role=PlayerRole.BATTER
        )
        make_player(
            full_name="Atlantis Bowler", birth_country=country, primary_role=PlayerRole.BOWLER
        )
        make_player(full_name="Other Batter", primary_role=PlayerRole.BATTER)
        assert list(list_players(country=str(country.pk), role=PlayerRole.BATTER)) == [batter]

    def test_get_player_is_n1_free(self) -> None:
        country = Country.objects.create(name="Atlantis")
        player = make_player(full_name="Player One", birth_country=country)
        with CaptureQueriesContext(connection) as queries:
            result = get_player(player.pk)
        assert result.birth_country.name == "Atlantis"
        assert len(queries) == 1

    def test_get_player_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_player("00000000-0000-7000-8000-000000000000")


class TestDuplicateCandidates:
    def test_orders_by_score_desc_and_filters_status(self) -> None:
        a = make_player(full_name="Player A")
        b = make_player(full_name="Player B")
        c = make_player(full_name="Player C")
        PlayerDuplicateCandidate.objects.create(
            candidate_player=a, canonical_player=b, score="0.700", feature_breakdown={}
        )
        PlayerDuplicateCandidate.objects.create(
            candidate_player=c, canonical_player=b, score="0.900", feature_breakdown={}
        )
        PlayerDuplicateCandidate.objects.create(
            candidate_player=b,
            canonical_player=a,
            score="0.800",
            feature_breakdown={},
            status="resolved",
        )
        queue = list(list_duplicate_candidates())
        assert [q.score for q in queue] == [decimal.Decimal("0.900"), decimal.Decimal("0.700")]
        assert [q.candidate_player.full_name for q in queue] == ["Player C", "Player A"]
