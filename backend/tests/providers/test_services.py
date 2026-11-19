"""Generic identity resolution (providers/services.resolve)."""

from __future__ import annotations

import datetime
import threading
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import connections

from apps.core.enums import TeamType
from apps.core.errors import Unprocessable
from apps.players.models import Player, PlayerDuplicateCandidate
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import get_resolver, resolve
from apps.teams.models import Team, TeamMembership
from apps.venues.models import Country

pytestmark = pytest.mark.django_db

DOB = datetime.date(1988, 11, 5)


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


@pytest.fixture
def country() -> Country:
    return Country.objects.create(name="Atlantis")


def _make_player(**overrides: object) -> Player:
    defaults: dict[str, object] = {
        "full_name": "Virat Kohli",
        "date_of_birth": DOB,
        "gender": "M",
    }
    defaults.update(overrides)
    return Player.objects.create(**defaults)  # type: ignore[arg-type]


class TestResolve:
    def test_exact_map_hit_returns_internal_id(self, provider: DataProvider) -> None:
        existing = _make_player()
        ProviderEntityMap.objects.create(
            provider=provider,
            entity_type="player",
            internal_id=existing.pk,
            provider_entity_id="opt-vk-1",
            verified=False,
        )

        result = resolve(
            provider,
            "player",
            "opt-vk-1",
            {"full_name": "Some Other Name", "date_of_birth": datetime.date(2000, 1, 1)},
        )
        assert result == existing.pk
        # The exact hit short-circuits: no match, no stub, no new rows.
        assert Player.objects.count() == 1
        assert ProviderEntityMap.objects.count() == 1

    def test_unique_high_confidence_candidate_is_linked(
        self, provider: DataProvider, country: Country
    ) -> None:
        team = Team.objects.create(name="Atlantis XI", team_type=TeamType.INTERNATIONAL)
        existing = _make_player(birth_country=country)
        TeamMembership.objects.create(team=team, player=existing)

        result = resolve(
            provider,
            "player",
            "opt-vk-1",
            {
                "full_name": "Virat Kohli",
                "date_of_birth": DOB,
                "gender": "M",
                "birth_country": "Atlantis",
                "birth_country_id": str(country.pk),
                "team_ids": [str(team.pk)],
            },
        )
        assert result == existing.pk
        assert Player.objects.count() == 1  # no stub created

        map_row = ProviderEntityMap.objects.get(
            provider=provider, entity_type="player", provider_entity_id="opt-vk-1"
        )
        assert map_row.internal_id == existing.pk
        assert map_row.verified is False
        assert map_row.confidence == Decimal("1.000")
        assert PlayerDuplicateCandidate.objects.count() == 0

    def test_sub_threshold_candidate_creates_stub_and_duplicate_flag(
        self, provider: DataProvider, country: Country
    ) -> None:
        # 0.90 candidate: name+dob+country+gender all match (0.85) plus a
        # shared-team dice of 1/3 (1 shared of 1 vs 5) -> exactly 0.90.
        shared = Team.objects.create(name="Atlantis XI", team_type=TeamType.INTERNATIONAL)
        other_teams = [
            Team.objects.create(name=f"Atlantis {i}", team_type=TeamType.DOMESTIC) for i in range(4)
        ]
        existing = _make_player(birth_country=country)
        TeamMembership.objects.create(team=shared, player=existing)
        for team in other_teams:
            TeamMembership.objects.create(team=team, player=existing)

        result = resolve(
            provider,
            "player",
            "opt-vk-1",
            {
                "full_name": "Virat Kohli",
                "date_of_birth": DOB,
                "gender": "M",
                "birth_country": "Atlantis",
                "birth_country_id": str(country.pk),
                "team_ids": [str(shared.pk)],
            },
        )

        # Never auto-merge/link: the map points at a NEW stub, not `existing`.
        assert result != existing.pk
        stub = Player.objects.get(pk=result)
        assert stub.pk != existing.pk
        assert stub.full_name == "Virat Kohli"
        assert Player.objects.count() == 2

        map_row = ProviderEntityMap.objects.get(
            provider=provider, entity_type="player", provider_entity_id="opt-vk-1"
        )
        assert map_row.internal_id == stub.pk

        flag = PlayerDuplicateCandidate.objects.get()
        assert flag.candidate_player_id == stub.pk
        assert flag.canonical_player_id == existing.pk
        assert flag.score == Decimal("0.900")
        assert flag.status == "open"

    def test_resolve_is_idempotent(self, provider: DataProvider) -> None:
        first = resolve(provider, "player", "opt-vk-1", {"full_name": "New Person"})
        second = resolve(provider, "player", "opt-vk-1", {"full_name": "New Person"})
        assert first == second
        assert Player.objects.count() == 1
        assert ProviderEntityMap.objects.count() == 1

    def test_unregistered_entity_type_is_rejected(self, provider: DataProvider) -> None:
        with pytest.raises(Unprocessable):
            resolve(provider, "umpire", "u-1", {"name": "X"})


class TestRegistry:
    def test_resolver_is_registered_for_players(self) -> None:
        resolver = get_resolver("player")
        assert resolver is not None


@pytest.mark.django_db(transaction=True)
def test_concurrent_resolve_creates_one_player_and_one_map() -> None:
    provider = DataProvider.objects.create(name="Test Provider", code="test-provider")
    attributes = {
        "full_name": "Virat Kohli",
        "date_of_birth": DOB,
        "gender": "M",
    }
    barrier = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(str(resolve(provider, "player", "opt-race-1", attributes)))
        except BaseException as exc:  # pragma: no cover - failure reporting
            errors.append(exc)
        finally:
            # Each thread owns a connection; close it so pytest-django can
            # drop the test database after the test.
            connections.close_all()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(set(results)) == 1  # both callers agree on one internal id
    assert Player.objects.count() == 1
    assert (
        ProviderEntityMap.objects.filter(
            provider=provider, entity_type="player", provider_entity_id="opt-race-1"
        ).count()
        == 1
    )
    assert settings.IDENTITY_MATCH_THRESHOLD == 0.95
