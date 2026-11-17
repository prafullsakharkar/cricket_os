"""Venue upsert service (keyed by (provider, provider_entity_id) via the map)."""

from __future__ import annotations

import pytest

from apps.providers.models import DataProvider, ProviderEntityMap
from apps.venues.models import Country, Venue
from apps.venues.services import upsert_venue

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


@pytest.fixture
def country() -> Country:
    return Country.objects.create(name="Atlantis")


def test_creates_venue_and_map_row(provider: DataProvider, country: Country) -> None:
    venue = upsert_venue(
        provider,
        "opt-venue-1",
        name="Atlantis Oval",
        city="Capitol",
        country_id=country.pk,
    )
    assert venue.name == "Atlantis Oval"
    assert venue.country_id == country.pk
    assert venue.source_provider_id == provider.pk
    assert venue.provider_venue_id == "opt-venue-1"

    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="venue", provider_entity_id="opt-venue-1"
    )
    assert map_row.internal_id == venue.pk
    assert map_row.verified is False


def test_run_twice_is_idempotent(provider: DataProvider) -> None:
    first = upsert_venue(provider, "opt-venue-1", name="Atlantis Oval")
    second = upsert_venue(provider, "opt-venue-1", name="Atlantis Oval")
    assert first.pk == second.pk
    assert Venue.objects.count() == 1
    assert ProviderEntityMap.objects.filter(entity_type="venue").count() == 1


def test_update_path_updates_fields(provider: DataProvider) -> None:
    venue = upsert_venue(provider, "opt-venue-1", name="Atlantis Oval", capacity=20_000)
    updated = upsert_venue(provider, "opt-venue-1", name="New Atlantis Oval", capacity=25_000)
    assert updated.pk == venue.pk
    updated.refresh_from_db()
    assert updated.name == "New Atlantis Oval"
    assert updated.capacity == 25_000
    assert Venue.objects.count() == 1


def test_name_based_upsert_never_matches_an_existing_venue(provider: DataProvider) -> None:
    # An existing venue with no provider map (e.g. created by hand). The new
    # upsert must NOT resolve to it by name alone — a distinct city keeps the
    # new venue legal under ux_venue_name_city (name, coalesce(city, '')).
    existing = Venue.objects.create(name="Atlantis Oval", source_provider_id=provider.pk)
    assert existing.provider_venue_id is None

    venue = upsert_venue(provider, "opt-venue-2", name="Atlantis Oval", city="Capitol")
    assert venue.pk != existing.pk
    assert venue.provider_venue_id == "opt-venue-2"
    assert Venue.objects.count() == 2


def test_upsert_without_provider_entity_id_creates_map_row(provider: DataProvider) -> None:
    venue = upsert_venue(provider, "opt-venue-3", name="Atlantis Oval")
    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="venue", provider_entity_id="opt-venue-3"
    )
    assert map_row.internal_id == venue.pk
