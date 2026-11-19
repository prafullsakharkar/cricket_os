"""Team serializer (read-only, sparse-field aware)."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.api import SparseFieldsMixin
from apps.teams.models import Team
from apps.venues.api.serializers import (
    CountryBriefSerializer,
    GoverningBodyBriefSerializer,
    VenueBriefSerializer,
)

TEAM_FIELDS = (
    "id",
    "name",
    "short_name",
    "abbreviation",
    "team_type",
    "gender",
    "is_international",
    "country",
    "governing_body",
    "home_venue",
)


class TeamSerializer(SparseFieldsMixin, serializers.ModelSerializer):  # type: ignore[type-arg]
    allowed_fields = TEAM_FIELDS
    country = CountryBriefSerializer(read_only=True)
    governing_body = GoverningBodyBriefSerializer(read_only=True)
    home_venue = VenueBriefSerializer(read_only=True)

    class Meta:
        model = Team
        fields = TEAM_FIELDS


__all__ = ["TeamSerializer"]
