"""Venue/Country/GoverningBody serializers (read-only, sparse-field aware).

The ``CountryBrief`` / ``GoverningBodyBrief`` / ``VenueBrief`` classes are
imported by the team/player/competition serializers for consistent minimal
nesting. Dates render as ISO-8601 (``YYYY-MM-DD``); the reference models carry
no timestamps (decision D1).
"""

from __future__ import annotations

from rest_framework import serializers

from apps.core.api import SparseFieldsMixin
from apps.venues.models import Country, GoverningBody, Venue


class CountryBriefSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = Country
        fields = ("id", "name", "iso_code", "iso3")


class GoverningBodyBriefSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = GoverningBody
        fields = ("id", "name", "abbreviation", "type")


class VenueBriefSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = Venue
        fields = ("id", "name", "city", "timezone")


VENUE_FIELDS = (
    "id",
    "name",
    "city",
    "country",
    "timezone",
    "capacity",
    "is_neutral",
    "latitude",
    "longitude",
)


class VenueSerializer(SparseFieldsMixin, serializers.ModelSerializer):  # type: ignore[type-arg]
    """Full venue representation for ``GET /api/v1/venues[/{id}]``."""

    allowed_fields = VENUE_FIELDS
    country = CountryBriefSerializer(read_only=True)

    class Meta:
        model = Venue
        fields = VENUE_FIELDS


__all__ = [
    "CountryBriefSerializer",
    "GoverningBodyBriefSerializer",
    "VenueBriefSerializer",
    "VenueSerializer",
]
