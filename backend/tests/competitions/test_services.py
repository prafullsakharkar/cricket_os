"""Competition + series upsert services (keyed by provider map)."""

from __future__ import annotations

import datetime

import pytest

from apps.competitions.models import Competition, Series
from apps.competitions.services import upsert_competition, upsert_series
from apps.core.enums import CompetitionType, FormatType
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.venues.models import Country

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


def test_upsert_competition_creates_row_and_map(provider: DataProvider) -> None:
    comp = upsert_competition(
        provider,
        "opt-comp-1",
        name="Atlantis Premier League",
        competition_type=CompetitionType.LEAGUE,
        format_type=FormatType.T20,
        is_international=False,
    )
    assert comp.name == "Atlantis Premier League"
    assert comp.competition_type == CompetitionType.LEAGUE
    assert comp.source_provider_id == provider.pk
    assert comp.provider_competition_id == "opt-comp-1"

    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="competition", provider_entity_id="opt-comp-1"
    )
    assert map_row.internal_id == comp.pk
    assert map_row.verified is False


def test_upsert_competition_run_twice_is_idempotent(provider: DataProvider) -> None:
    first = upsert_competition(
        provider,
        "opt-comp-1",
        name="Atlantis Premier League",
        competition_type=CompetitionType.LEAGUE,
    )
    second = upsert_competition(
        provider,
        "opt-comp-1",
        name="Atlantis Premier League",
        competition_type=CompetitionType.LEAGUE,
    )
    assert first.pk == second.pk
    assert Competition.objects.count() == 1


def test_upsert_competition_update_path_updates_fields(provider: DataProvider) -> None:
    comp = upsert_competition(
        provider,
        "opt-comp-1",
        name="Atlantis Premier League",
        competition_type=CompetitionType.LEAGUE,
    )
    updated = upsert_competition(
        provider,
        "opt-comp-1",
        name="Atlantis T20 League",
        competition_type=CompetitionType.LEAGUE,
        format_type=FormatType.T20,
    )
    assert updated.pk == comp.pk
    updated.refresh_from_db()
    assert updated.name == "Atlantis T20 League"
    assert updated.format_type == FormatType.T20


def test_upsert_series_creates_row_and_map(provider: DataProvider) -> None:
    country = Country.objects.create(name="Atlantis")
    series = upsert_series(
        provider,
        "opt-series-1",
        name="Atlantis vs Avalon",
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 1, 15),
        host_country_id=country.pk,
    )
    assert series.name == "Atlantis vs Avalon"
    assert series.source_provider_id == provider.pk
    assert series.provider_series_id == "opt-series-1"

    map_row = ProviderEntityMap.objects.get(
        provider=provider, entity_type="series", provider_entity_id="opt-series-1"
    )
    assert map_row.internal_id == series.pk
    assert map_row.verified is False


def test_upsert_series_run_twice_is_idempotent(provider: DataProvider) -> None:
    first = upsert_series(provider, "opt-series-1", name="Atlantis vs Avalon")
    second = upsert_series(provider, "opt-series-1", name="Atlantis vs Avalon")
    assert first.pk == second.pk
    assert Series.objects.count() == 1


def test_upsert_series_update_path_updates_fields(provider: DataProvider) -> None:
    series = upsert_series(provider, "opt-series-1", name="Atlantis vs Avalon")
    updated = upsert_series(
        provider,
        "opt-series-1",
        name="Atlantis vs Avalon ODI",
        series_type="bilateral",
        start_date=datetime.date(2026, 2, 1),
    )
    assert updated.pk == series.pk
    updated.refresh_from_db()
    assert updated.name == "Atlantis vs Avalon ODI"
    assert updated.start_date == datetime.date(2026, 2, 1)
