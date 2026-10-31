"""Venue-domain model constraints (DATABASE_SCHEMA §3.2)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.venues.models import Country, GoverningBody, Ground, Venue

pytestmark = pytest.mark.django_db


class TestCountry:
    # NOTE: the seed data migration already inserts the ICC full members, so
    # these tests use names/codes that no seed row uses.
    def test_duplicate_name_rejected(self) -> None:
        Country.objects.create(name="Atlantis")
        with pytest.raises(IntegrityError):
            Country.objects.create(name="Atlantis")

    def test_duplicate_iso_code_rejected(self) -> None:
        Country.objects.create(name="Atlantis", iso_code="ZZ")
        with pytest.raises(IntegrityError):
            Country.objects.create(name="Avalon", iso_code="ZZ")

    def test_multiple_rows_may_have_null_iso(self) -> None:
        Country.objects.create(name="Atlantis")
        Country.objects.create(name="Avalon")
        assert Country.objects.filter(iso_code__isnull=True).count() == 2

    def test_iso_validator_rejects_bad_code(self) -> None:
        country = Country(name="Atlantis", iso_code="IN1", iso3="ZZZ")
        with pytest.raises(ValidationError):
            country.full_clean()

    def test_iso_validator_accepts_good_code(self) -> None:
        country = Country(name="Atlantis", iso_code="ZZ", iso3="ZZZ")
        country.full_clean()  # must not raise


class TestGoverningBody:
    def test_duplicate_name_rejected(self) -> None:
        GoverningBody.objects.create(name="Cricket Atlantis", type="national")
        with pytest.raises(IntegrityError):
            GoverningBody.objects.create(name="Cricket Atlantis", type="national")

    def test_type_check_constraint(self) -> None:
        with pytest.raises(IntegrityError):
            GoverningBody.objects.create(name="X", type="galactic")


class TestVenue:
    def test_same_name_and_city_rejected(self) -> None:
        country = Country.objects.create(name="Atlantis")
        Venue.objects.create(name="Olympic Ground", city="New Atlantis", country=country)
        with pytest.raises(IntegrityError):
            Venue.objects.create(name="Olympic Ground", city="New Atlantis", country=country)

    def test_null_city_coalesces_to_empty(self) -> None:
        """(name, coalesce(city,'')) is unique, so two same-name NULL-city rows clash."""
        Venue.objects.create(name="The Oval")
        with pytest.raises(IntegrityError):
            Venue.objects.create(name="The Oval")

    def test_same_name_different_city_allowed(self) -> None:
        Venue.objects.create(name="Eden Gardens", city="Kolkata")
        Venue.objects.create(name="Eden Gardens", city="Other")
        assert Venue.objects.count() == 2

    def test_different_names_with_null_city_allowed(self) -> None:
        Venue.objects.create(name="Lord's")
        Venue.objects.create(name="The Oval")
        assert Venue.objects.count() == 2

    def test_timezone_validator_rejects_unknown_zone(self) -> None:
        venue = Venue(name="Mars Stadium", timezone="Mars/Olympus")
        with pytest.raises(ValidationError):
            venue.full_clean()

    def test_timezone_validator_accepts_iana_zone(self) -> None:
        Venue.objects.create(name="Eden Gardens", city="Kolkata", timezone="Asia/Kolkata")
        assert Venue.objects.get(name="Eden Gardens").timezone == "Asia/Kolkata"


class TestGround:
    def test_unique_venue_and_name(self) -> None:
        venue = Venue.objects.create(name="Lord's")
        Ground.objects.create(venue=venue, name="Main Ground")
        with pytest.raises(IntegrityError):
            Ground.objects.create(venue=venue, name="Main Ground")

    def test_same_ground_name_at_different_venues_allowed(self) -> None:
        a = Venue.objects.create(name="Lord's")
        b = Venue.objects.create(name="The Oval")
        Ground.objects.create(venue=a, name="Main Ground")
        Ground.objects.create(venue=b, name="Main Ground")
        assert Ground.objects.count() == 2
