"""PlayerResolver: match ranking + stub creation with duplicate flags."""

from __future__ import annotations

import datetime

import pytest

from apps.core.enums import TeamType
from apps.players.models import Player, PlayerDuplicateCandidate
from apps.players.services.identity_resolution import PlayerResolver
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.teams.models import Team, TeamMembership

pytestmark = pytest.mark.django_db

DOB = datetime.date(1988, 11, 5)


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


@pytest.fixture
def resolver() -> PlayerResolver:
    return PlayerResolver()


def _player(**overrides: object) -> Player:
    defaults: dict[str, object] = {"full_name": "Virat Kohli", "date_of_birth": DOB, "gender": "M"}
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


class TestMatch:
    def test_exact_match_ranks_first(self, resolver: PlayerResolver) -> None:
        _player(full_name="Steve Smith")
        exact = _player(full_name="Virat Kohli")
        _player(full_name="Virat Kohli Junior")

        candidates = resolver.match(
            {"full_name": "Virat Kohli", "date_of_birth": DOB, "gender": "M"}
        )
        assert candidates[0].internal_id == exact.pk
        # name 1.0 + dob 1.0 + country missing 0.5 + gender 1.0, no teams.
        assert candidates[0].score == pytest.approx(0.775)

    def test_filters_by_dob(self, resolver: PlayerResolver) -> None:
        expected = _player(full_name="Virat Kohli", date_of_birth=DOB)
        _player(full_name="Virat Kohli", date_of_birth=datetime.date(1990, 1, 1))
        candidates = resolver.match(
            {"full_name": "Virat Kohli", "date_of_birth": DOB, "gender": "M"}
        )
        assert [c.internal_id for c in candidates] == [expected.pk]

    def test_filters_by_gender(self, resolver: PlayerResolver) -> None:
        _player(full_name="Alex Morgan", gender="W")
        _player(full_name="Alex Morgan", gender="M")
        candidates = resolver.match({"full_name": "Alex Morgan", "gender": "W"})
        assert all(candidate is not None for candidate in candidates)
        # The M row is filtered out entirely.
        assert len(candidates) == 1

    def test_excludes_inactive_and_deleted(self, resolver: PlayerResolver) -> None:
        from django.utils import timezone

        _player(full_name="Virat Kohli", is_active=False)
        _player(full_name="Virat Kohli", deleted_at=timezone.now())
        assert resolver.match({"full_name": "Virat Kohli"}) == []

    def test_missing_full_name_returns_empty(self, resolver: PlayerResolver) -> None:
        assert resolver.match({"date_of_birth": DOB}) == []

    def test_shared_teams_raise_score(self, resolver: PlayerResolver) -> None:
        team = Team.objects.create(name="Atlantis XI", team_type=TeamType.INTERNATIONAL)
        existing = _player(full_name="Virat Kohli")
        TeamMembership.objects.create(team=team, player=existing)

        candidates = resolver.match(
            {
                "full_name": "Virat Kohli",
                "date_of_birth": DOB,
                "gender": "M",
                "team_ids": [str(team.pk)],
            }
        )
        # name 1.0 + dob 1.0 + country missing 0.5 + gender 1.0 + teams 1.0.
        assert candidates[0].score == pytest.approx(0.925)


class TestCreateStub:
    def test_creates_player_and_map_row(
        self, resolver: PlayerResolver, provider: DataProvider
    ) -> None:
        stub_id = resolver.create_stub(
            provider, "opt-new-1", {"full_name": "Brand New Player", "gender": "M"}
        )
        stub = Player.objects.get(pk=stub_id)
        assert stub.full_name == "Brand New Player"
        assert stub.is_active is True
        assert stub.source_provider_id == provider.pk
        assert stub.provider_player_id == "opt-new-1"

        map_row = ProviderEntityMap.objects.get(
            provider=provider, entity_type="player", provider_entity_id="opt-new-1"
        )
        assert map_row.internal_id == stub_id
        assert map_row.verified is False

    def test_flags_duplicate_candidates_above_threshold(
        self, resolver: PlayerResolver, provider: DataProvider
    ) -> None:
        existing = _player(full_name="Virat Kohli", date_of_birth=DOB, gender="M")
        stub_id = resolver.create_stub(
            provider,
            "opt-new-1",
            {
                "full_name": "Virat Kohli",
                "date_of_birth": DOB,
                "gender": "M",
            },
        )
        stub = Player.objects.get(pk=stub_id)
        flag = PlayerDuplicateCandidate.objects.get()
        assert flag.candidate_player_id == stub.pk
        assert flag.canonical_player_id == existing.pk
        assert flag.status == "open"

    def test_stub_does_not_match_itself(
        self, resolver: PlayerResolver, provider: DataProvider
    ) -> None:
        resolver.create_stub(provider, "opt-new-1", {"full_name": "Nobody Else", "gender": "M"})
        # No duplicate candidate rows: the stub matched only itself.
        assert PlayerDuplicateCandidate.objects.count() == 0

    def test_stub_is_idempotent_through_resolve(
        self, resolver: PlayerResolver, provider: DataProvider
    ) -> None:
        from apps.providers.services import resolve

        first = resolve(provider, "player", "opt-new-1", {"full_name": "Brand New Player"})
        second = resolve(provider, "player", "opt-new-1", {"full_name": "Brand New Player"})
        assert first == second
        assert Player.objects.count() == 1
        assert ProviderEntityMap.objects.count() == 1
