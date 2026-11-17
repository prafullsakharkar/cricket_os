"""Venue-domain selectors (read paths, soft-delete defaults, N+1-free)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.errors import NotFound
from apps.venues.models import Country, GoverningBody, Venue
from apps.venues.selectors import (
    get_country,
    get_governing_body,
    get_venue,
    list_countries,
    list_governing_bodies,
    list_venues,
)

pytestmark = pytest.mark.django_db


class TestListCountries:
    def test_active_only(self) -> None:
        # The seed migration already inserts the 11 ICC full members.
        active = Country.objects.create(name="Atlantis")
        hidden = Country.objects.create(name="Avalon", is_active=False)
        rows = list(list_countries())
        assert active in rows
        assert hidden not in rows
        assert Country.objects.filter(is_active=True).count() == len(rows)

    def test_ordered_by_name(self) -> None:
        Country.objects.create(name="Zzzedland")
        Country.objects.create(name="Atlantis")
        names = [c.name for c in list_countries()]
        assert names.index("Atlantis") < names.index("Zzzedland")

    def test_get_country_not_found_for_inactive(self) -> None:
        hidden = Country.objects.create(name="Avalon", is_active=False)
        with pytest.raises(NotFound):
            get_country(hidden.pk)


class TestVenueSelectors:
    def test_list_venues_filters_and_loads_country(self) -> None:
        country = Country.objects.create(name="Atlantis")
        Venue.objects.create(name="Atlantis Oval", country=country)
        Venue.objects.create(name="Capitol Ground", country=country)
        other = Country.objects.create(name="Avalon")
        Venue.objects.create(name="Avalon Park", country=other)

        qs = list_venues(country_id=country.pk)
        assert list(qs.values_list("name", flat=True)) == ["Atlantis Oval", "Capitol Ground"]

    def test_get_venue_is_n1_free(self) -> None:
        country = Country.objects.create(name="Atlantis")
        venue = Venue.objects.create(name="Atlantis Oval", country=country)
        with CaptureQueriesContext(connection) as queries:
            result = get_venue(venue.pk)
        assert result.country.name == "Atlantis"
        assert len(queries) == 1  # select_related country, no per-row lookups

    def test_get_venue_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_venue("00000000-0000-7000-8000-000000000000")


class TestGoverningBodySelectors:
    def test_list_loads_country(self) -> None:
        # The seed migration already inserts 13 governing bodies.
        country = Country.objects.create(name="Atlantis")
        body = GoverningBody.objects.create(
            name="Atlantis Cricket Board", country=country, type="national"
        )
        with CaptureQueriesContext(connection) as queries:
            rows = list(list_governing_bodies())
        assert body in rows
        assert len(queries) == 1  # select_related country, no per-row lookups

    def test_get_governing_body_missing_raises_not_found(self) -> None:
        with pytest.raises(NotFound):
            get_governing_body("00000000-0000-7000-8000-000000000000")
