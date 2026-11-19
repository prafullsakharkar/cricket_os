"""Team upsert service (keyed by (provider, provider_entity_id) via the map)."""

from __future__ import annotations

import pytest

from apps.core.enums import TeamType
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.teams.models import Team
from apps.teams.services import upsert_team
from apps.venues.models import Country

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


def test_creates_team_and_map_row(provider: DataProvider) -> None:
    team = upsert_team(
        provider,
        "opt-team-1",
        name="Atlantis Royals",
        team_type=TeamType.DOMESTIC,
    )
    assert team.name == "Atlantis Royals"
    assert team.team_type == TeamType.DOMESTIC
    assert team.source_provider_id == provider.pk
    assert team.provider_team_id == "opt-team-1"
    assert team.is_active is True

    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="team", provider_entity_id="opt-team-1"
    )
    assert map_row.internal_id == team.pk
    assert map_row.verified is False


def test_run_twice_is_idempotent(provider: DataProvider) -> None:
    first = upsert_team(provider, "opt-team-1", name="Atlantis Royals", team_type=TeamType.DOMESTIC)
    second = upsert_team(
        provider, "opt-team-1", name="Atlantis Royals", team_type=TeamType.DOMESTIC
    )
    assert first.pk == second.pk
    assert Team.objects.count() == 1
    assert ProviderEntityMap.objects.filter(entity_type="team").count() == 1


def test_update_path_updates_fields(provider: DataProvider) -> None:
    country = Country.objects.create(name="Atlantis")
    team = upsert_team(provider, "opt-team-1", name="Atlantis Royals", team_type=TeamType.DOMESTIC)
    updated = upsert_team(
        provider,
        "opt-team-1",
        name="Atlantis Kings",
        team_type=TeamType.FRANCHISE,
        country_id=country.pk,
    )
    assert updated.pk == team.pk
    updated.refresh_from_db()
    assert updated.name == "Atlantis Kings"
    assert updated.team_type == TeamType.FRANCHISE
    assert updated.country_id == country.pk


def test_name_based_upsert_never_matches_an_existing_team(provider: DataProvider) -> None:
    existing = Team.objects.create(name="Atlantis Royals", team_type=TeamType.DOMESTIC)
    team = upsert_team(provider, "opt-team-2", name="Atlantis Royals", team_type=TeamType.DOMESTIC)
    assert team.pk != existing.pk
    assert Team.objects.count() == 2
