"""Django admin: registration, page loads, duplicate-review actions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.admin import site
from django.contrib.auth.models import User
from django.test import Client, RequestFactory

from apps.administration.models import AuditLog
from apps.competitions.models import Competition, FormatCatalogue, MatchStage, Season, Series
from apps.core import events
from apps.core.enums import TeamType
from apps.players.models import Player, PlayerDuplicateCandidate, PlayerMerge
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.teams.models import Squad, SquadMember, Team, TeamMembership
from apps.venues.models import Country, GoverningBody, Ground, Venue

pytestmark = pytest.mark.django_db

DUPLICATE_CHANGELIST = "/admin/players/playerduplicatecandidate/"


@pytest.fixture
def admin_user() -> User:
    return User.objects.create_superuser(username="admin", password="pw", email="admin@example.com")


@pytest.fixture
def client(admin_user: User) -> Client:
    client = Client()
    client.force_login(admin_user)
    return client


def make_candidate(
    *, winner: Player | None = None, loser: Player | None = None, score: str = "0.950"
) -> PlayerDuplicateCandidate:
    winner = winner or Player.objects.create(full_name="Virat Kohli")
    loser = loser or Player.objects.create(full_name="V Kohli")
    return PlayerDuplicateCandidate.objects.create(
        candidate_player=loser,
        canonical_player=winner,
        score=Decimal(score),
        feature_breakdown={
            "name": {"score": 1.0, "weight": 0.35, "contribution": 0.35},
            "date_of_birth": {"score": 1.0, "weight": 0.25, "contribution": 0.25},
        },
    )


class TestRegistration:
    def test_every_reference_model_is_registered(self) -> None:
        expected = {
            Country,
            GoverningBody,
            Venue,
            Ground,
            Team,
            TeamMembership,
            Squad,
            Player,
            PlayerMerge,
            PlayerDuplicateCandidate,
            Competition,
            Season,
            Series,
            MatchStage,
            FormatCatalogue,
            DataProvider,
            ProviderEntityMap,
            AuditLog,
        }
        for model in expected:
            assert model in site._registry, f"{model} not registered"
        # SquadMember is covered by the SquadAdmin inline (plan Task 5.1:
        # "SquadAdmin/SquadMemberInline"), not a standalone admin.
        from apps.teams.admin import SquadAdmin, SquadMemberInline

        assert SquadAdmin.inlines == (SquadMemberInline,)
        assert SquadMemberInline.model is SquadMember

    def test_duplicate_queue_has_all_three_actions(self, admin_user: User) -> None:
        admin = site._registry[PlayerDuplicateCandidate]
        request = RequestFactory().get("/admin/players/playerduplicatecandidate/")
        request.user = admin_user
        actions = set(admin.get_actions(request))
        assert {"confirm_merge", "reject_candidate", "verify_mapping"} <= actions


class TestChangePagesLoad:
    def test_reference_model_change_pages_render(self, client: Client) -> None:
        country = Country.objects.create(name="Atlantis")
        body = GoverningBody.objects.create(
            name="Atlantis Cricket Board", country=country, type="national"
        )
        venue = Venue.objects.create(name="Atlantis Oval", country=country)
        Ground.objects.create(name="Main Ground", venue=venue)
        team = Team.objects.create(name="Atlantis XI", team_type=TeamType.INTERNATIONAL)
        player = Player.objects.create(full_name="Virat Kohli", birth_country=country)
        TeamMembership.objects.create(team=team, player=player)
        squad = Squad.objects.create(team=team, name="2026")
        SquadMember.objects.create(squad=squad, player=player)
        competition = Competition.objects.create(
            name="Atlantis League",
            competition_type="LEAGUE",
            format_type="T20",
        )
        season = Season.objects.create(competition=competition, label="2026")
        series = Series.objects.create(name="Atlantis Cup", competition=competition)
        MatchStage.objects.create(series=series, name="Final", stage_type="final")
        provider = DataProvider.objects.create(name="Test Provider", code="test-provider")
        ProviderEntityMap.objects.create(
            provider=provider,
            entity_type="player",
            internal_id=player.pk,
            provider_entity_id="opt-vk-1",
        )
        AuditLog.objects.create(action="test", entity_type="player")

        urls = [
            f"/admin/venues/country/{country.pk}/change/",
            f"/admin/venues/governingbody/{body.pk}/change/",
            f"/admin/venues/venue/{venue.pk}/change/",
            f"/admin/venues/ground/{Ground.objects.get().pk}/change/",
            f"/admin/teams/team/{team.pk}/change/",
            f"/admin/teams/teammembership/{TeamMembership.objects.get().pk}/change/",
            f"/admin/teams/squad/{squad.pk}/change/",
            f"/admin/players/player/{player.pk}/change/",
            "/admin/players/playermerge/",
            f"/admin/competitions/competition/{competition.pk}/change/",
            f"/admin/competitions/season/{season.pk}/change/",
            f"/admin/competitions/series/{series.pk}/change/",
            f"/admin/competitions/matchstage/{MatchStage.objects.get().pk}/change/",
            "/admin/competitions/formatcatalogue/",
            f"/admin/providers/dataprovider/{provider.pk}/change/",
            "/admin/providers/providerentitymap/",
            "/admin/administration/auditlog/",
        ]
        for url in urls:
            response = client.get(url)
            assert response.status_code == 200, f"{url} returned {response.status_code}"

    def test_player_change_page_shows_provider_map_inline(self, client: Client) -> None:
        provider = DataProvider.objects.create(name="Test Provider", code="test-provider")
        player = Player.objects.create(full_name="Virat Kohli")
        ProviderEntityMap.objects.create(
            provider=provider,
            entity_type="player",
            internal_id=player.pk,
            provider_entity_id="opt-vk-1",
            verified=True,
        )
        response = client.get(f"/admin/players/player/{player.pk}/change/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "opt-vk-1" in content
        assert "Provider identity maps" in content


class TestDuplicateReviewActions:
    def test_confirm_merge_confirmation_shows_breakdown(self, client: Client) -> None:
        candidate = make_candidate()
        response = client.post(
            DUPLICATE_CHANGELIST,
            {
                "action": "confirm_merge",
                "_selected_action": [str(candidate.pk)],
                "index": "0",
            },
        )
        assert response.status_code == 200
        content = response.content.decode()
        assert "Feature breakdown" in content
        assert "date_of_birth" in content  # a feature name from the breakdown

    def test_confirm_merge_flow(self, client: Client) -> None:
        candidate = make_candidate()
        winner = candidate.canonical_player
        loser = candidate.candidate_player

        response = client.post(
            DUPLICATE_CHANGELIST,
            {
                "action": "confirm_merge",
                "_selected_action": [str(candidate.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302  # back to the changelist

        merge = PlayerMerge.objects.get()
        assert merge.canonical_player_id == winner.pk
        assert merge.merged_player_id == loser.pk
        candidate.refresh_from_db()
        assert candidate.status == "merged"
        loser.refresh_from_db()
        assert loser.is_active is False
        # Audited synchronously (on_commit=False).
        assert AuditLog.objects.filter(action=events.TOPIC_PLAYER_MERGED).count() == 1

    def test_reject_candidate_flow(self, client: Client) -> None:
        candidate = make_candidate()
        response = client.post(
            DUPLICATE_CHANGELIST,
            {
                "action": "reject_candidate",
                "_selected_action": [str(candidate.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302
        candidate.refresh_from_db()
        assert candidate.status == "resolved"
        assert candidate.reviewed_at is not None
        assert AuditLog.objects.filter(action=events.TOPIC_DUPLICATE_REVIEWED).count() == 1
        assert PlayerMerge.objects.count() == 0

    def test_verify_mapping_flow(self, client: Client) -> None:
        provider = DataProvider.objects.create(name="Test Provider", code="test-provider")
        candidate = make_candidate()
        map_row = ProviderEntityMap.objects.create(
            provider=provider,
            entity_type="player",
            internal_id=candidate.candidate_player_id,
            provider_entity_id="opt-vk-1",
            verified=False,
        )
        response = client.post(
            DUPLICATE_CHANGELIST,
            {
                "action": "verify_mapping",
                "_selected_action": [str(candidate.pk)],
                "index": "0",
                "confirm": "yes",
            },
        )
        assert response.status_code == 302
        map_row.refresh_from_db()
        assert map_row.verified is True
        assert AuditLog.objects.filter(action=events.TOPIC_PROVIDER_MAP_VERIFIED).count() == 1
