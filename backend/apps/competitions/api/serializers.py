"""Competition / Season / Series serializers (read-only, sparse-field aware).

``CompetitionSerializer`` (list) omits seasons; ``CompetitionDetailSerializer``
adds them so the detail view stays at 2 queries and the list at 1.
"""

from __future__ import annotations

from rest_framework import serializers

from apps.competitions.models import Competition, Season, Series
from apps.core.api import SparseFieldsMixin
from apps.venues.api.serializers import (
    CountryBriefSerializer,
    GoverningBodyBriefSerializer,
)


class SeasonBriefSerializer(serializers.ModelSerializer):  # type: ignore[type-arg]
    class Meta:
        model = Season
        fields = ("id", "label", "start_date", "end_date")


COMPETITION_FIELDS = (
    "id",
    "name",
    "short_name",
    "competition_type",
    "format_type",
    "is_international",
    "gender",
    "governing_body",
)


class CompetitionSerializer(SparseFieldsMixin, serializers.ModelSerializer):  # type: ignore[type-arg]
    allowed_fields: tuple[str, ...] = COMPETITION_FIELDS
    governing_body = GoverningBodyBriefSerializer(read_only=True)

    class Meta:
        model = Competition
        fields: tuple[str, ...] = COMPETITION_FIELDS


COMPETITION_DETAIL_FIELDS: tuple[str, ...] = (*COMPETITION_FIELDS, "seasons")


class CompetitionDetailSerializer(CompetitionSerializer):
    """Competition + nested seasons (``GET /api/v1/competitions/{id}``)."""

    allowed_fields: tuple[str, ...] = COMPETITION_DETAIL_FIELDS
    seasons = SeasonBriefSerializer(many=True, read_only=True)

    class Meta(CompetitionSerializer.Meta):
        fields = COMPETITION_DETAIL_FIELDS


SERIES_FIELDS = (
    "id",
    "name",
    "short_name",
    "series_type",
    "is_tournament",
    "start_date",
    "end_date",
    "competition",
    "season",
    "host_country",
)


class SeriesSerializer(SparseFieldsMixin, serializers.ModelSerializer):  # type: ignore[type-arg]
    allowed_fields = SERIES_FIELDS
    competition = serializers.SerializerMethodField()
    season = serializers.SerializerMethodField()
    host_country = CountryBriefSerializer(read_only=True)

    class Meta:
        model = Series
        fields = SERIES_FIELDS

    def get_competition(self, obj: Series) -> dict[str, str] | None:
        if obj.competition is None:
            return None
        return {"id": str(obj.competition.pk), "name": obj.competition.name}

    def get_season(self, obj: Series) -> dict[str, str] | None:
        if obj.season is None:
            return None
        return {"id": str(obj.season.pk), "label": obj.season.label}


__all__ = [
    "CompetitionDetailSerializer",
    "CompetitionSerializer",
    "SeasonBriefSerializer",
    "SeriesSerializer",
]
