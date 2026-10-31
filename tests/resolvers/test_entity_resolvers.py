"""Non-player entity resolvers through the generic ``resolve()``.

``apps.providers.services.resolve`` is the single entry point every provider
importer uses: exact map hit → link to a unique >= 0.95 candidate → create a
stub. The player path is covered by tests/players/test_identity_resolution.py;
this module covers the team / venue / competition / series resolvers
(registered from each app's ``ready()``) end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.competitions.models import Competition, Series
from apps.providers.models import DataProvider, ProviderEntityMap
from apps.providers.services import resolve
from apps.teams.models import Team
from apps.venues.models import Country, Venue

pytestmark = pytest.mark.django_db


@pytest.fixture
def provider() -> DataProvider:
    return DataProvider.objects.create(name="Test Provider", code="test-provider")


def _map_row(provider: DataProvider, entity_type: str, provider_id: str) -> ProviderEntityMap:
    return ProviderEntityMap.objects.get(
        provider=provider, entity_type=entity_type, provider_entity_id=provider_id
    )


class TestTeamResolver:
    def test_exact_map_hit_is_idempotent(self, provider: DataProvider) -> None:
        first = resolve(provider, "team", "opt-1", {"name": "Atlantis Royals"})
        second = resolve(provider, "team", "opt-1", {"name": "Atlantis Royals"})
        assert first == second
        assert Team.objects.count() == 1
        assert ProviderEntityMap.objects.filter(entity_type="team").count() == 1

    def test_creates_stub_when_no_match(self, provider: DataProvider) -> None:
        team_id = resolve(
            provider,
            "team",
            "opt-1",
            {"name": "Atlantis Royals", "team_type": "franchise", "country_id": None},
        )
        team = Team.objects.get(pk=team_id)
        assert team.name == "Atlantis Royals"
        assert team.team_type == "franchise"
        assert team.source_provider_id == provider.pk
        row = _map_row(provider, "team", "opt-1")
        assert row.internal_id == team_id
        assert row.verified is False
        assert row.confidence == 1.0

    def test_links_to_normalized_name_match(self, provider: DataProvider) -> None:
        existing = Team.objects.create(name="Atlantis Royals", team_type="franchise")
        # Whitespace/case variants must match the existing row.
        resolved = resolve(provider, "team", "opt-9", {"name": "  atlantis  royals "})
        assert resolved == existing.pk
        assert Team.objects.count() == 1
        assert _map_row(provider, "team", "opt-9").internal_id == existing.pk

    def test_match_qualifier_filters_by_country(self, provider: DataProvider) -> None:
        ca = Country.objects.create(name="Canada", iso_code="CA")
        other = Country.objects.create(name="Other", iso_code="OT")
        home = Team.objects.create(name="Canada XI", team_type="international", country=ca)
        Team.objects.create(name="Canada XI", team_type="international", country=other)

        from apps.teams.services import TeamResolver

        resolver = TeamResolver()
        candidates = resolver.match({"name": "Canada XI", "country_id": str(ca.pk)})
        assert [c.internal_id for c in candidates] == [home.pk]
        assert resolver.match({"name": ""}) == []


class TestVenueResolver:
    def test_creates_stub_when_no_match(self, provider: DataProvider) -> None:
        venue_id = resolve(provider, "venue", "opt-1", {"name": "Atlantis Oval", "city": "Capitol"})
        venue = Venue.objects.get(pk=venue_id)
        assert venue.name == "Atlantis Oval"
        assert venue.city == "Capitol"
        assert venue.timezone == "UTC"
        assert _map_row(provider, "venue", "opt-1").internal_id == venue_id

    def test_links_to_normalized_name_match(self, provider: DataProvider) -> None:
        existing = Venue.objects.create(name="Atlantis Oval", city="Capitol")
        resolved = resolve(provider, "venue", "opt-2", {"name": "Atlantis Oval", "city": "Capitol"})
        assert resolved == existing.pk
        assert Venue.objects.count() == 1

    def test_match_qualifier_filters_by_city(self, provider: DataProvider) -> None:
        want = Venue.objects.create(name="Atlantis Oval", city="Capitol")
        Venue.objects.create(name="Atlantis Oval", city="Elsewhere")

        from apps.venues.services import VenueResolver

        resolver = VenueResolver()
        candidates = resolver.match({"name": "Atlantis Oval", "city": "Capitol"})
        assert [c.internal_id for c in candidates] == [want.pk]
        assert resolver.match({"name": None}) == []


class TestCompetitionResolver:
    def test_creates_stub_when_no_match(self, provider: DataProvider) -> None:
        comp_id = resolve(provider, "competition", "opt-1", {"name": "Atlantis Cup"})
        comp = Competition.objects.get(pk=comp_id)
        assert comp.name == "Atlantis Cup"
        assert comp.competition_type == "LEAGUE"  # create_stub default
        assert _map_row(provider, "competition", "opt-1").internal_id == comp_id

    def test_links_to_normalized_name_match(self, provider: DataProvider) -> None:
        existing = Competition.objects.create(name="Atlantis Cup", competition_type="LEAGUE")
        resolved = resolve(provider, "competition", "opt-2", {"name": "atlantis cup"})
        assert resolved == existing.pk
        assert Competition.objects.count() == 1


class TestSeriesResolver:
    def test_creates_stub_when_no_match(self, provider: DataProvider) -> None:
        series_id = resolve(provider, "series", "opt-1", {"name": "Atlantis Series 2026"})
        series = Series.objects.get(pk=series_id)
        assert series.name == "Atlantis Series 2026"
        assert series.series_type == "bilateral"
        assert _map_row(provider, "series", "opt-1").internal_id == series_id

    def test_links_to_normalized_name_match_with_start_year(self, provider: DataProvider) -> None:
        import datetime

        existing = Series.objects.create(
            name="Atlantis Series 2026", start_date=datetime.date(2026, 6, 1)
        )
        resolved = resolve(
            provider,
            "series",
            "opt-2",
            {"name": "Atlantis Series 2026", "start_date": datetime.date(2026, 6, 15)},
        )
        assert resolved == existing.pk
        assert Series.objects.count() == 1

    def test_start_year_qualifier_excludes_other_years(self, provider: DataProvider) -> None:
        import datetime

        want = Series.objects.create(name="Atlantis Cup", start_date=datetime.date(2026, 1, 1))
        Series.objects.create(name="Atlantis Cup", start_date=datetime.date(2025, 1, 1))

        from apps.competitions.services import SeriesResolver

        resolver = SeriesResolver()
        candidates = resolver.match(
            {"name": "Atlantis Cup", "start_date": datetime.date(2026, 7, 1)}
        )
        assert [c.internal_id for c in candidates] == [want.pk]
        assert resolver.match({"name": None}) == []


class TestRegistration:
    @pytest.mark.parametrize(
        ("entity_type", "attrs"),
        [
            ("team", {"name": "Unique Team Name", "team_type": "other"}),
            ("venue", {"name": "Unique Venue Name"}),
            ("competition", {"name": "Unique Competition Name"}),
            ("series", {"name": "Unique Series Name"}),
        ],
    )
    def test_all_entity_types_resolve(
        self, provider: DataProvider, entity_type: str, attrs: dict[str, Any]
    ) -> None:
        internal_id = resolve(provider, entity_type, f"id-{entity_type}", attrs)
        assert (
            ProviderEntityMap.objects.filter(
                provider=provider, entity_type=entity_type, provider_entity_id=f"id-{entity_type}"
            ).count()
            == 1
        )
        assert internal_id is not None


__all__: list[str] = []
